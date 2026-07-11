"""统一的网络重试工具：指数退避 + 抖动。

用于百炼视觉识别与配音等外部 API 调用。默认对瞬时错误（超时、连接失败、
5xx、429、以及未知 SDK 异常）重试，对明确的致命错误（鉴权/参数/404）快速失败，
避免对一个必然失败的请求空转。
"""

from __future__ import annotations

import random
import time
import urllib.error
from typing import Callable, TypeVar

T = TypeVar("T")

# 明确不该重试的 HTTP 状态（鉴权/参数/资源不存在）
_FATAL_HTTP = {400, 401, 403, 404, 422}
# 明确不该重试的错误关键词
_FATAL_KEYWORDS = (
    "unauthorized", "api key", "apikey", "invalid api", "forbidden",
    "not found", "permission", "no such file", "filenotfound",
    "参数错误", "鉴权", "密钥", "无权",
)


def is_transient(exc: BaseException) -> bool:
    """判断异常是否为可重试的瞬时错误。"""
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code not in _FATAL_HTTP
    if isinstance(exc, (urllib.error.URLError, TimeoutError, ConnectionError, OSError)):
        return True
    text = str(exc).lower()
    if any(keyword in text for keyword in _FATAL_KEYWORDS):
        return False
    return True


def retry_call(
    fn: Callable[[], T],
    *,
    attempts: int = 4,
    base_delay: float = 2.0,
    max_delay: float = 30.0,
    jitter: float = 0.3,
    retry_on: Callable[[BaseException], bool] = is_transient,
    on_retry: Callable[[int, BaseException, float], None] | None = None,
    label: str = "",
) -> T:
    """执行 ``fn``，失败时按指数退避重试。

    - attempts：总尝试次数（含首次）。
    - base_delay：首次退避基数秒；第 n 次退避 = base_delay * 2**(n-1)，封顶 max_delay。
    - jitter：在退避时间上叠加 [0, delay*jitter) 的随机抖动，避免同时重试的雷同。
    - retry_on：判定异常是否重试；默认 is_transient。
    - on_retry(attempt, exc, delay)：每次重试前回调（可用于打印日志）。
    """
    last_exc: BaseException | None = None
    for attempt in range(1, max(1, attempts) + 1):
        try:
            return fn()
        except BaseException as exc:  # noqa: BLE001 — 交给 retry_on 决策
            last_exc = exc
            if attempt >= attempts or not retry_on(exc):
                raise
            delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
            delay += random.uniform(0.0, delay * max(0.0, jitter))
            if on_retry is not None:
                on_retry(attempt, exc, delay)
            time.sleep(delay)
    assert last_exc is not None
    raise last_exc
