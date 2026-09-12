"""
Akasha-RAG 单终端聚合启动器 (Unified Process Launcher)

功能：
1. 在同一个 CMD/终端窗口中同时并发启动后端 (FastAPI/Uvicorn) 和前端 (Vite)
2. 统一流式输出：自动为输出添加彩色标签 [BACKEND] 与 [FRONTEND]
3. 本地日志文件固化：所有终端输出实时追加保存至 logs/terminal_YYYY-MM-DD.log，防终端刷屏漏报
4. 优雅退出：按 Ctrl+C 即可一键安全终止前后端子进程，无僵尸进程遗留
"""
from __future__ import annotations

import datetime
import os
import re
import signal
import subprocess
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from launcher_i18n import t as _t  # noqa: E402  多语言文案（AKASHA_LANG 切换）

# 剥离子进程输出里的 ANSI 转义序列（CSI 颜色/光标 + OSC 超链接等），
# 仅用于写入日志文件，终端仍保留彩色。
_ANSI_ESCAPE_RE = re.compile(
    r"\x1b\[[0-9;?]*[ -/]*[@-~]"      # CSI: \x1b[ ... 最终字节
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"  # OSC: \x1b] ... BEL 或 ST
    r"|\x1b[@-Z\\-_]"                  # 其它双字符转义
)


def _strip_ansi(text: str) -> str:
    return _ANSI_ESCAPE_RE.sub("", text)


# ANSI 终端颜色
CYAN = "\033[96m"
MAGENTA = "\033[95m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
BOLD = "\033[1m"
UNDERLINE = "\033[4m"
RESET = "\033[0m"


def make_clickable_link(url: str, text: str | None = None) -> str:
    """
    生成终端可点击链接（支持标准 OSC 8 超链接规范及下划线，兼容 Windows Terminal / VS Code / CMD）
    """
    label = text or url
    return f"\033]8;;{url}\033\\{UNDERLINE}{label}\033]8;;\033\\"


def _display_width(text: str) -> int:
    """Return console cell width for the launcher banner's mixed CJK labels."""
    import unicodedata

    return sum(2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1 for char in text)


def _banner_row(label: str, value: str, suffix: str = "", color: str = "") -> str:
    """Keep URL columns aligned even when labels contain CJK characters."""
    label_width = 8
    padding = " " * max(0, label_width - _display_width(label))
    return f"{BOLD}  - {label}{padding}: {color}{value}{RESET}{suffix}"


# Windows 控制台 ANSI 颜色与 UTF-8 编码支持，并禁用快速编辑模式防止假死
if sys.platform == "win32":
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        # 启用输出 ANSI 颜色支持
        kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
        # 彻底关闭 CMD 快速编辑模式（QuickEdit Mode: 0x0040），防止用户鼠标误触控制台导致标准输出挂起、前后端服务假死
        h_in = kernel32.GetStdHandle(-10)
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(h_in, ctypes.byref(mode)):
            new_mode = (mode.value & ~0x0040) | 0x0080
            kernel32.SetConsoleMode(h_in, new_mode)
    except Exception:
        pass
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = ROOT_DIR / "backend"
FRONTEND_DIR = ROOT_DIR / "frontend"
LOGS_DIR = ROOT_DIR / "logs"


def unsupported_platform_message(
    platform: str | None = None,
    windows_version: tuple[int, int] | None = None,
) -> str | None:
    """Return a user-facing compatibility error before any service is started."""
    current_platform = platform or sys.platform
    if current_platform == "darwin":
        return "Unsupported operating system: macOS. Akasha-RAG currently supports Windows only."
    if current_platform.startswith("linux"):
        return "Unsupported operating system: Linux. Akasha-RAG currently supports Windows only."
    if current_platform != "win32":
        return f"Unsupported operating system: {current_platform}. Akasha-RAG currently supports Windows only."

    if windows_version is None:
        version = sys.getwindowsversion()
        windows_version = (version.major, version.minor)
    # Floor is Windows 10, matching scripts/bootstrap.ps1 (Test-SupportedWindows).
    # The runtimes the one-click setup installs (CPython 3.12, Node 22) do not
    # support Windows 7/8/8.1, so accepting them here would only defer a clear
    # refusal into a confusing failure further down the line.
    if windows_version < (10, 0):
        return (
            "Unsupported operating system: Windows 8.1 or older. "
            "Akasha-RAG requires Windows 10 or newer."
        )
    return None


class UnifiedLauncher:
    def __init__(self) -> None:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        self.log_file_path = LOGS_DIR / f"terminal_{today}.log"
        self.log_file = open(self.log_file_path, "a", encoding="utf-8", buffering=1)
        self.lock = threading.Lock()
        self.backend_proc: subprocess.Popen | None = None
        self.frontend_proc: subprocess.Popen | None = None
        self.stopping = False
        self.exit_code = 0

        # 服务就绪状态与自动打开浏览器控制
        self.backend_ready = False
        self.backend_ready_event = threading.Event()
        self.frontend_ready = False
        self.browser_opened = False
        self.browser_lock = threading.Lock()

    def log_message(self, prefix: str, text: str, color: str = "") -> None:
        line = text.rstrip("\r\n")
        if not line:
            return
        now_str = datetime.datetime.now().strftime("%H:%M:%S")
        formatted_console = f"{color}[{now_str}] {prefix} {line}{RESET}"
        # 日志文件去除子进程自带的 ANSI 转义序列，避免编辑器里出现 ESC[32m 之类乱码
        formatted_file = f"[{now_str}] {prefix} {_strip_ansi(line)}\n"

        with self.lock:
            try:
                print(formatted_console, flush=True)
            except Exception:
                try:
                    safe_console = formatted_console.encode(sys.stdout.encoding or "utf-8", errors="replace").decode(sys.stdout.encoding or "utf-8")
                    print(safe_console, flush=True)
                except Exception:
                    pass

            try:
                self.log_file.write(formatted_file)
                self.log_file.flush()
            except Exception:
                pass

    def _check_and_open_browser(self) -> None:
        """当后端和前端均准备就绪时，自动在默认浏览器中打开页面"""
        with self.browser_lock:
            if self.backend_ready and self.frontend_ready and not self.browser_opened and not self.stopping:
                self.browser_opened = True
                frontend_url = "http://localhost:5173"
                self.log_message("[READY]", _t("ready_opened", url=frontend_url), GREEN + BOLD)
                import webbrowser
                try:
                    webbrowser.open(frontend_url)
                except Exception as e:
                    self.log_message("[SYSTEM]", _t("browser_open_failed", err=e), YELLOW)

    def _fallback_browser_opener(self) -> None:
        """兜底守护线程：若服务启动 3 秒后未被终端输出触发，主动探测端口连通性并自动打开浏览器"""
        import time
        import urllib.request
        import webbrowser

        time.sleep(3.0)
        for _ in range(15):
            if self.stopping:
                return
            with self.browser_lock:
                if self.browser_opened:
                    return
            try:
                req = urllib.request.Request("http://127.0.0.1:5173", headers={"User-Agent": "Akasha-RAG-Probe"})
                with urllib.request.urlopen(req, timeout=1.0) as resp:
                    if resp.status in (200, 304):
                        with self.browser_lock:
                            if not self.browser_opened and not self.stopping:
                                self.browser_opened = True
                                self.log_message(
                                    "[READY]",
                                    _t("ready_opened", url="http://localhost:5173"),
                                    GREEN + BOLD,
                                )
                                webbrowser.open("http://localhost:5173")
                                return
            except Exception:
                pass
            time.sleep(1.0)

    def stream_reader(self, pipe, prefix: str, color: str) -> None:
        try:
            for raw_line in iter(pipe.readline, b""):
                if not raw_line or self.stopping:
                    break
                try:
                    text = raw_line.decode("utf-8")
                except UnicodeDecodeError:
                    text = raw_line.decode("gbk", errors="replace")

                # 彻底过滤 Windows CMD 批处理提示（避免混入输出或干扰终端）
                if "终止批处理操作" in text or "Terminate batch job" in text:
                    continue

                self.log_message(prefix, text, color)

                # 实时探测前后端就绪输出
                # 只认 "Application startup complete"：--reload 下 "Uvicorn running"
                # 由 reloader 进程提前打印，此时 worker 尚未绑定端口。
                if prefix == "[BACKEND]" and "Application startup complete" in text:
                    self.backend_ready = True
                    self.backend_ready_event.set()

                if not self.browser_opened:
                    if prefix == "[BACKEND]" and self.backend_ready:
                        self._check_and_open_browser()
                    elif prefix == "[FRONTEND]" and ("ready in" in text or "Local:" in text or "5173" in text):
                        self.frontend_ready = True
                        self._check_and_open_browser()
        except Exception:
            pass
        finally:
            pipe.close()

    def start_backend(self) -> subprocess.Popen:
        python_exe = BACKEND_DIR / ".venv" / "Scripts" / "python.exe"
        if not python_exe.exists():
            python_exe = Path(sys.executable)

        cmd = [
            str(python_exe),
            "-m",
            "uvicorn",
            "app.main:app",
            "--reload",
            "--host",
            "127.0.0.1",
            "--port",
            "8000",
        ]
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"

        return subprocess.Popen(
            cmd,
            cwd=str(BACKEND_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
        )

    def start_frontend(self) -> subprocess.Popen:
        # 检查是否已安装 node_modules
        node_modules = FRONTEND_DIR / "node_modules"
        npm_cmd = "npm.cmd" if sys.platform == "win32" else "npm"

        if not node_modules.exists():
            self.log_message("[SETUP]", _t("installing_frontend_deps"), YELLOW)
            subprocess.run([npm_cmd, "install"], cwd=str(FRONTEND_DIR), check=True)

        # 优先直接以 Node 执行 Vite 二进制脚本，绕过 npm.cmd 批处理包装层，彻底杜绝 Windows CMD '终止批处理操作吗' 弹窗
        vite_bin = node_modules / "vite" / "bin" / "vite.js"
        if vite_bin.exists():
            import shutil
            node_exe = shutil.which("node") or "node"
            cmd = [node_exe, str(vite_bin)]
        else:
            cmd = [npm_cmd, "run", "dev"]

        return subprocess.Popen(
            cmd,
            cwd=str(FRONTEND_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )

    def shutdown(self, *, wait_for_key: bool = True) -> None:
        if self.stopping:
            return
        self.stopping = True
        self.log_message("[SYSTEM]", _t("shutting_down"), YELLOW)

        for name, proc in [("frontend", self.frontend_proc), ("backend", self.backend_proc)]:
            if proc and proc.poll() is None:
                try:
                    if sys.platform == "win32":
                        subprocess.run(
                            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                        )
                    else:
                        proc.terminate()
                except Exception:
                    pass

        self.log_message("[SYSTEM]", _t("all_exited"), GREEN)
        try:
            self.log_file.close()
        except Exception:
            pass

        if wait_for_key:
            self._wait_before_exit()

    def _wait_before_exit(self) -> None:
        """Show the final status without forcibly closing the host terminal."""
        print(f"\n{BOLD}{GREEN}================================================================{RESET}")
        print(f"{BOLD}{GREEN}  {_t('all_exited')}{RESET}")
        print(f"{BOLD}{YELLOW}  {_t('press_any_key')}{RESET}")
        print(f"{BOLD}{GREEN}================================================================{RESET}\n", flush=True)

        if sys.platform == "win32":
            import time
            import msvcrt
            import ctypes

            # 稍微沉淀 200ms，确保终端管道所有缓冲输出落盘
            time.sleep(0.2)
            try:
                # 清空键盘输入缓冲区残留（如用户运行期间误按的字符或 Ctrl+C 信号键）
                while msvcrt.kbhit():
                    msvcrt.getch()
            except Exception:
                pass

            try:
                # 阻塞等待用户按下任意键（按任意按键即刻响应，无需按 Enter 或输入 Y/N）
                msvcrt.getch()
            except Exception:
                pass

            return
        else:
            try:
                input(_t("press_enter"))
            except Exception:
                pass
            sys.exit(0)

    def run(self) -> int:
        link_fe = make_clickable_link("http://localhost:5173", "http://localhost:5173")
        link_be = make_clickable_link("http://127.0.0.1:8000", "http://127.0.0.1:8000")
        link_docs = make_clickable_link("http://127.0.0.1:8000/docs", "http://127.0.0.1:8000/docs")

        print(f"\n{BOLD}{GREEN}================================================================{RESET}")
        print(f"{BOLD}{GREEN}  {_t('banner_title')}{RESET}")
        print(f"{BOLD}{GREEN}================================================================{RESET}")
        print(_banner_row(_t('label_frontend'), link_fe, f"  {GREEN}({_t('hint_autoopen')}){RESET}", CYAN))
        print(_banner_row(_t('label_backend'), link_be, f"  {GREEN}({_t('hint_ctrlclick')}){RESET}", CYAN))
        print(_banner_row(_t('label_apidoc'), link_docs, f"  {GREEN}({_t('hint_ctrlclick')}){RESET}", CYAN))
        print(_banner_row(_t('label_log'), str(self.log_file_path), color=YELLOW))
        print(_banner_row(_t('label_exit'), _t('hint_ctrlc', key=f'{RED}Ctrl + C{RESET}')))
        print(f"{BOLD}{GREEN}================================================================{RESET}\n")

        # 启动后端
        self.log_message("[BACKEND]", _t("starting_backend"), CYAN)
        self.backend_proc = self.start_backend()
        t_b = threading.Thread(
            target=self.stream_reader,
            args=(self.backend_proc.stdout, "[BACKEND]", CYAN),
            daemon=True,
        )
        t_b.start()

        # 等后端完全就绪再起前端，避免 Vite 代理在 8000 端口未响应时刷 ECONNREFUSED
        for _ in range(30):
            if self.backend_ready_event.wait(timeout=1.0) or self.stopping:
                break
            if self.backend_proc and self.backend_proc.poll() is not None:
                code = self.backend_proc.poll()
                self.exit_code = 1
                self.log_message("[BACKEND]", _t("backend_exited", code=code), RED)
                self.shutdown(wait_for_key=False)
                return self.exit_code
        else:
            self.log_message("[SYSTEM]", _t("backend_not_ready"), YELLOW)

        # 启动前端
        if self.stopping:
            return self.exit_code
        self.log_message("[FRONTEND]", _t("starting_frontend"), MAGENTA)
        try:
            self.frontend_proc = self.start_frontend()
        except (OSError, subprocess.CalledProcessError) as exc:
            self.exit_code = 1
            self.log_message("[FRONTEND]", f"Failed to start frontend: {exc}", RED)
            self.shutdown(wait_for_key=False)
            return self.exit_code
        t_f = threading.Thread(
            target=self.stream_reader,
            args=(self.frontend_proc.stdout, "[FRONTEND]", MAGENTA),
            daemon=True,
        )
        t_f.start()

        # 启动就绪探测与自动打开浏览器兜底守护线程
        t_browser = threading.Thread(
            target=self._fallback_browser_opener,
            daemon=True,
            name="browser-auto-opener",
        )
        t_browser.start()

        # 信号捕获
        def handle_signal(sig, frame):
            try:
                signal.signal(signal.SIGINT, signal.SIG_IGN)
                signal.signal(signal.SIGTERM, signal.SIG_IGN)
            except Exception:
                pass
            self.shutdown()

        signal.signal(signal.SIGINT, handle_signal)
        signal.signal(signal.SIGTERM, handle_signal)

        try:
            while not self.stopping:
                # 检查进程是否异常退出
                if self.backend_proc and self.backend_proc.poll() is not None:
                    code = self.backend_proc.poll()
                    self.exit_code = 1
                    self.log_message("[BACKEND]", _t("backend_exited", code=code), RED)
                    break
                if self.frontend_proc and self.frontend_proc.poll() is not None:
                    code = self.frontend_proc.poll()
                    self.exit_code = 1
                    self.log_message("[FRONTEND]", _t("frontend_exited", code=code), RED)
                    break
                threading.Event().wait(1)
        except KeyboardInterrupt:
            pass
        finally:
            self.shutdown()
        return self.exit_code


if __name__ == "__main__":
    try:
        platform_error = unsupported_platform_message()
        if platform_error:
            print(f"{BOLD}{RED}{platform_error}{RESET}")
            sys.exit(2)
        launcher = UnifiedLauncher()
        sys.exit(launcher.run())
    except Exception as exc:
        import traceback
        print(f"\n{BOLD}{RED}================================================================{RESET}")
        print(f"{BOLD}{RED}  {_t('crash_banner')}{RESET}")
        print(f"{BOLD}{RED}================================================================{RESET}")
        traceback.print_exc()
        print(f"{BOLD}{RED}================================================================{RESET}\n")
        try:
            if sys.platform == "win32":
                import msvcrt
                print(f"{BOLD}{YELLOW}{_t('press_any_key')}{RESET}")
                msvcrt.getch()
            else:
                input(_t("press_enter"))
        except Exception:
            pass
        sys.exit(1)
