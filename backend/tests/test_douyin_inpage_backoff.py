"""
NET-03 回归测试：抖音页面内请求重试改成固定递增退避

这段重试逻辑跑在 `page.evaluate` 传入的 JS 字符串里，不是 Python 代码——
没有真实浏览器环境无法验证"退避真的按预期发生"，这里只做结构性断言，
证明代码确实改了、两处重试循环都引用了同一个退避序列、且不再有写死的
800ms 延迟。真实网络行为需要人工在真实登录状态下跑一次同步验证
（NEEDS_MANUAL_RUNTIME_VERIFICATION，不能靠这个单测证明）。
"""
import inspect

from app.services.douyin_collector import DouyinCollector


def _fetch_in_context_source() -> str:
    return inspect.getsource(DouyinCollector._fetch_in_context)


def test_no_stale_fixed_800ms_delay_remains():
    source = _fetch_in_context_source()
    assert "800" not in source


def test_retry_delay_sequence_is_defined_once_and_referenced_by_both_loops():
    source = _fetch_in_context_source()
    assert source.count("const RETRY_DELAYS_MS") == 1
    assert source.count("RETRY_DELAYS_MS[retry]") == 2


def test_no_wait_is_scheduled_after_the_final_attempt():
    """三次尝试、两次退避——`retry < 2` 保证只在还有下一次尝试时才等待。"""
    source = _fetch_in_context_source()
    assert source.count("if (retry < 2)") == 2


def test_backoff_applies_to_both_thrown_errors_and_non_zero_status_code():
    """退避判断挪到 try/catch 外面，不再只在 catch 分支里等待——确认两处
    重试循环各自的 catch 分支不再直接调用 setTimeout（那会导致非零
    statusCode 的失败响应跳过退避、直接进入下一次尝试）。只检查
    `retry < 3` 重试循环自己的 catch 分支，不管方法里其它无关的 try/catch
    （比如模块探测那段）。"""
    source = _fetch_in_context_source()
    retry_loops = source.split("for (let retry = 0; retry < 3; retry++) {")[1:]
    assert len(retry_loops) == 2
    for loop_body in retry_loops:
        catch_body = loop_body.split("catch(e) {", 1)[1].split("}", 1)[0]
        assert "setTimeout" not in catch_body
