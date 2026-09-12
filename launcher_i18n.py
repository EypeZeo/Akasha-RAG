"""启动器多语言文案。

语言选择优先级：环境变量 AKASHA_LANG (zh/en/ja/fr/de/ko/ru/hi) > 系统 locale 探测 > en。
中文/日文系统按 locale 命中 zh/ja，其余一律 en（面向海外开源用户）。
"""
from __future__ import annotations

import locale
import os

SUPPORTED = ("zh", "en", "ja", "fr", "de", "ko", "ru", "hi")


def resolve_lang() -> str:
    env = (os.environ.get("AKASHA_LANG") or "").strip().lower()
    if env in SUPPORTED:
        return env
    if env[:2] in SUPPORTED:
        return env[:2]
    probe = ""
    try:
        probe = (locale.getlocale()[0] or "").lower()
    except Exception:
        probe = ""
    probe = f"{probe} {os.environ.get('LANG', '').lower()}"
    if "zh" in probe or "chinese" in probe:
        return "zh"
    if "ja" in probe or "japan" in probe:
        return "ja"
    if "ko" in probe or "korea" in probe:
        return "ko"
    if "fr" in probe or "french" in probe:
        return "fr"
    if "de" in probe or "german" in probe:
        return "de"
    if "ru" in probe or "russ" in probe:
        return "ru"
    if "hi" in probe or "hind" in probe:
        return "hi"
    return "en"


# key -> {lang: text}。text 可含 {url} / {err} / {code} 占位符。
_STRINGS: dict[str, dict[str, str]] = {
    "banner_title": {
        "zh": "Akasha-RAG 聚合启动器 (前后端单终端并行 + 日志固化)",
        "en": "Akasha-RAG launcher (backend + frontend in one terminal, logs persisted)",
        "ja": "Akasha-RAG ランチャー（バックエンド + フロントを 1 ターミナルで並行 / ログ保存）",
        "fr": "Lanceur Akasha-RAG (backend + frontend dans un terminal, logs conservés)",
        "de": "Akasha-RAG Launcher (Backend + Frontend in einem Terminal, Logs gespeichert)",
        "ko": "Akasha-RAG 런처 (백엔드 + 프런트엔드 단일 터미널, 로그 저장)",
        "ru": "Запуск Akasha-RAG (бэкенд + фронтенд в одном терминале, логи сохраняются)",
        "hi": "Akasha-RAG लॉन्चर (बैकएंड + फ्रंटएंड एक टर्मिनल में, लॉग सहेजे जाते हैं)",
    },
    "label_frontend": {"zh": "前端界面", "en": "Frontend", "ja": "フロントエンド", "fr": "Frontend", "de": "Frontend", "ko": "프런트엔드", "ru": "Фронтенд", "hi": "फ्रंटएंड"},
    "label_backend": {"zh": "后端接口", "en": "Backend API", "ja": "バックエンド API", "fr": "API backend", "de": "Backend-API", "ko": "백엔드 API", "ru": "Backend API", "hi": "बैकएंड API"},
    "label_apidoc": {"zh": "API 文档", "en": "API docs", "ja": "API ドキュメント", "fr": "Doc API", "de": "API-Doku", "ko": "API 문서", "ru": "Документация API", "hi": "API दस्तावेज़"},
    "label_log": {"zh": "本地日志", "en": "Local log", "ja": "ローカルログ", "fr": "Journal local", "de": "Lokales Log", "ko": "로컬 로그", "ru": "Локальный лог", "hi": "स्थानीय लॉग"},
    "label_exit": {"zh": "退出方式", "en": "To quit", "ja": "終了方法", "fr": "Pour quitter", "de": "Beenden", "ko": "종료 방법", "ru": "Выход", "hi": "बाहर निकलें"},
    "hint_autoopen": {
        "zh": "就绪自动弹出浏览器 / 支持 Ctrl+Click 点击",
        "en": "opens the browser automatically when ready / Ctrl+Click supported",
        "ja": "準備完了でブラウザ自動起動 / Ctrl+Click 対応",
        "fr": "ouvre le navigateur automatiquement / Ctrl+Clic pris en charge",
        "de": "öffnet den Browser automatisch / Strg+Klick unterstützt",
        "ko": "준비되면 브라우저 자동 실행 / Ctrl+클릭 지원",
        "ru": "браузер откроется автоматически / поддерживается Ctrl+клик",
        "hi": "तैयार होने पर ब्राउज़र स्वतः खुलता है / Ctrl+Click समर्थित",
    },
    "hint_ctrlclick": {
        "zh": "支持 Ctrl+Click 点击", "en": "Ctrl+Click supported", "ja": "Ctrl+Click 対応",
        "fr": "Ctrl+Clic pris en charge", "de": "Strg+Klick unterstützt", "ko": "Ctrl+클릭 지원",
        "ru": "поддерживается Ctrl+клик", "hi": "Ctrl+Click समर्थित",
    },
    "hint_ctrlc": {
        "zh": "按 {key} 即可同时退出前后端所有服务",
        "en": "press {key} to stop both backend and frontend",
        "ja": "{key} でバックエンドとフロントを同時に終了",
        "fr": "appuyez sur {key} pour tout arrêter",
        "de": "{key} drücken, um Backend und Frontend zu beenden",
        "ko": "{key} 를 누르면 백엔드와 프런트엔드가 모두 종료됩니다",
        "ru": "нажмите {key}, чтобы остановить бэкенд и фронтенд",
        "hi": "दोनों को बंद करने के लिए {key} दबाएँ",
    },
    "starting_backend": {
        "zh": "正在启动 FastAPI 后端服务...", "en": "Starting FastAPI backend...", "ja": "FastAPI バックエンドを起動中...",
        "fr": "Démarrage du backend FastAPI...", "de": "FastAPI-Backend wird gestartet...", "ko": "FastAPI 백엔드 시작 중...",
        "ru": "Запуск бэкенда FastAPI...", "hi": "FastAPI बैकएंड शुरू हो रहा है...",
    },
    "starting_frontend": {
        "zh": "正在启动 Vite 前端服务...", "en": "Starting Vite frontend...", "ja": "Vite フロントエンドを起動中...",
        "fr": "Démarrage du frontend Vite...", "de": "Vite-Frontend wird gestartet...", "ko": "Vite 프런트엔드 시작 중...",
        "ru": "Запуск фронтенда Vite...", "hi": "Vite फ्रंटएंड शुरू हो रहा है...",
    },
    "installing_frontend_deps": {
        "zh": "正在安装前端依赖 (npm install)...", "en": "Installing frontend deps (npm install)...",
        "ja": "フロントエンド依存関係をインストール中 (npm install)...",
        "fr": "Installation des dépendances frontend (npm install)...",
        "de": "Frontend-Abhängigkeiten werden installiert (npm install)...",
        "ko": "프런트엔드 의존성 설치 중 (npm install)...",
        "ru": "Установка зависимостей фронтенда (npm install)...",
        "hi": "फ्रंटएंड निर्भरताएँ इंस्टॉल हो रही हैं (npm install)...",
    },
    "backend_not_ready": {
        "zh": "后端 30s 内未就绪，仍继续启动前端（代理可能短暂报错）",
        "en": "Backend not ready in 30s; starting frontend anyway (proxy may error briefly)",
        "ja": "30 秒以内にバックエンドが準備できませんでした。フロントを起動します（プロキシが一時的にエラーになる場合あり）",
        "fr": "Backend non prêt en 30 s ; démarrage du frontend quand même (le proxy peut échouer brièvement)",
        "de": "Backend nach 30 s nicht bereit; Frontend wird trotzdem gestartet (Proxy kann kurz Fehler zeigen)",
        "ko": "30초 내에 백엔드가 준비되지 않음; 프런트엔드를 계속 시작합니다 (프록시가 잠시 오류를 낼 수 있음)",
        "ru": "Бэкенд не готов за 30 с; фронтенд запускается всё равно (прокси может кратко ошибаться)",
        "hi": "30 सेकंड में बैकएंड तैयार नहीं; फिर भी फ्रंटएंड शुरू किया जा रहा है (प्रॉक्सी कुछ देर त्रुटि दे सकता है)",
    },
    "ready_opened": {
        "zh": "前后端服务已全部启动就绪！已自动为您在浏览器中打开: {url}",
        "en": "All services are up! Opened in your browser: {url}",
        "ja": "すべてのサービスが起動しました！ブラウザで開きました: {url}",
        "fr": "Tous les services sont lancés ! Ouvert dans le navigateur : {url}",
        "de": "Alle Dienste laufen! Im Browser geöffnet: {url}",
        "ko": "모든 서비스가 준비되었습니다! 브라우저에서 열었습니다: {url}",
        "ru": "Все сервисы запущены! Открыто в браузере: {url}",
        "hi": "सभी सेवाएँ चालू हैं! ब्राउज़र में खोला गया: {url}",
    },
    "browser_open_failed": {
        "zh": "自动打开浏览器失败: {err}，请按住 Ctrl 并点击上方链接访问",
        "en": "Could not open the browser: {err}. Ctrl+Click the link above.",
        "ja": "ブラウザを自動で開けませんでした: {err}。上のリンクを Ctrl+Click してください。",
        "fr": "Impossible d'ouvrir le navigateur : {err}. Ctrl+Clic sur le lien ci-dessus.",
        "de": "Browser konnte nicht geöffnet werden: {err}. Strg+Klick auf den Link oben.",
        "ko": "브라우저를 열지 못했습니다: {err}. 위 링크를 Ctrl+클릭하세요.",
        "ru": "Не удалось открыть браузер: {err}. Ctrl+клик по ссылке выше.",
        "hi": "ब्राउज़र नहीं खुल सका: {err}. ऊपर दिए लिंक पर Ctrl+Click करें।",
    },
    "shutting_down": {
        "zh": "正在安全终止前端与后端子进程...", "en": "Shutting down backend and frontend...",
        "ja": "バックエンドとフロントを安全に終了しています...",
        "fr": "Arrêt du backend et du frontend...", "de": "Backend und Frontend werden beendet...",
        "ko": "백엔드와 프런트엔드를 종료하는 중...", "ru": "Завершение работы бэкенда и фронтенда...",
        "hi": "बैकएंड और फ्रंटएंड बंद किए जा रहे हैं...",
    },
    "all_exited": {
        "zh": "前后端服务已全部安全退出。", "en": "All services have stopped.",
        "ja": "すべてのサービスが停止しました。", "fr": "Tous les services sont arrêtés.",
        "de": "Alle Dienste wurden beendet.", "ko": "모든 서비스가 중지되었습니다.",
        "ru": "Все сервисы остановлены.", "hi": "सभी सेवाएँ बंद हो गईं।",
    },
    "press_any_key": {
        "zh": "按任意键关闭窗口...", "en": "Press any key to close this window...",
        "ja": "任意のキーを押して閉じてください...", "fr": "Appuyez sur une touche pour fermer...",
        "de": "Beliebige Taste zum Schließen drücken...", "ko": "아무 키나 눌러 창을 닫으세요...",
        "ru": "Нажмите любую клавишу, чтобы закрыть окно...", "hi": "विंडो बंद करने के लिए कोई भी कुंजी दबाएँ...",
    },
    "press_enter": {
        "zh": "请按回车键 (Enter) 退出窗口...", "en": "Press Enter to exit...",
        "ja": "Enter キーを押して終了してください...", "fr": "Appuyez sur Entrée pour quitter...",
        "de": "Enter drücken zum Beenden...", "ko": "Enter 를 눌러 종료하세요...",
        "ru": "Нажмите Enter для выхода...", "hi": "बाहर निकलने के लिए Enter दबाएँ...",
    },
    "backend_exited": {
        "zh": "后端进程意外退出 (Exit Code: {code})", "en": "Backend process exited unexpectedly (exit code {code})",
        "ja": "バックエンドプロセスが予期せず終了しました (終了コード {code})",
        "fr": "Le processus backend s'est arrêté de façon inattendue (code {code})",
        "de": "Backend-Prozess unerwartet beendet (Exit-Code {code})",
        "ko": "백엔드 프로세스가 예기치 않게 종료됨 (종료 코드 {code})",
        "ru": "Процесс бэкенда неожиданно завершился (код {code})",
        "hi": "बैकएंड प्रक्रिया अप्रत्याशित रूप से बंद हुई (एग्ज़िट कोड {code})",
    },
    "frontend_exited": {
        "zh": "前端进程意外退出 (Exit Code: {code})", "en": "Frontend process exited unexpectedly (exit code {code})",
        "ja": "フロントエンドプロセスが予期せず終了しました (終了コード {code})",
        "fr": "Le processus frontend s'est arrêté de façon inattendue (code {code})",
        "de": "Frontend-Prozess unerwartet beendet (Exit-Code {code})",
        "ko": "프런트엔드 프로세스가 예기치 않게 종료됨 (종료 코드 {code})",
        "ru": "Процесс фронтенда неожиданно завершился (код {code})",
        "hi": "फ्रंटएंड प्रक्रिया अप्रत्याशित रूप से बंद हुई (एग्ज़िट कोड {code})",
    },
    "crash_banner": {
        "zh": "启动器运行时发生严重异常 (CRASH)", "en": "Launcher crashed with a fatal error",
        "ja": "ランチャーで致命的なエラーが発生しました (CRASH)",
        "fr": "Le lanceur a planté avec une erreur fatale",
        "de": "Launcher mit schwerem Fehler abgestürzt",
        "ko": "런처가 치명적 오류로 중단되었습니다",
        "ru": "Лаунчер аварийно завершился с фатальной ошибкой",
        "hi": "लॉन्चर एक गंभीर त्रुटि के साथ क्रैश हो गया",
    },
    # NOTE: the former "preflight_*" keys were removed in v0.7.5.  start.bat now
    # delegates every preflight step to scripts/bootstrap.ps1, which runs *before*
    # backend/.venv is guaranteed to exist -- so it cannot shell out to Python to
    # look up a string.  bootstrap.ps1 carries its own small EN/ZH table instead.
    # Everything below this point runs after the venv is ready and stays 8-language.
}

_LANG = resolve_lang()


def t(msg_id: str, **kw) -> str:
    entry = _STRINGS.get(msg_id, {})
    text = entry.get(_LANG) or entry.get("en") or msg_id
    return text.format(**kw) if kw else text
