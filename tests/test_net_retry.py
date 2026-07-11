from __future__ import annotations

import unittest
import urllib.error

from backend import net_retry
from backend.net_retry import is_transient, retry_call


class IsTransientTests(unittest.TestCase):
    def test_http_5xx_and_429_are_transient(self) -> None:
        for code in (429, 500, 502, 503, 504):
            exc = urllib.error.HTTPError("u", code, "msg", {}, None)
            self.assertTrue(is_transient(exc), code)

    def test_http_auth_and_param_errors_are_fatal(self) -> None:
        for code in (400, 401, 403, 404, 422):
            exc = urllib.error.HTTPError("u", code, "msg", {}, None)
            self.assertFalse(is_transient(exc), code)

    def test_network_errors_are_transient(self) -> None:
        self.assertTrue(is_transient(urllib.error.URLError("boom")))
        self.assertTrue(is_transient(TimeoutError("timed out")))
        self.assertTrue(is_transient(ConnectionError("reset")))

    def test_keyword_fatal_errors_not_retried(self) -> None:
        self.assertFalse(is_transient(RuntimeError("invalid api key")))
        self.assertFalse(is_transient(RuntimeError("鉴权失败")))

    def test_unknown_errors_default_transient(self) -> None:
        self.assertTrue(is_transient(RuntimeError("something odd happened")))


class RetryCallTests(unittest.TestCase):
    def setUp(self) -> None:
        # 不真正 sleep，加速测试并记录退避序列
        self._delays: list[float] = []
        self._orig_sleep = net_retry.time.sleep
        net_retry.time.sleep = lambda d: self._delays.append(d)  # type: ignore[assignment]

    def tearDown(self) -> None:
        net_retry.time.sleep = self._orig_sleep  # type: ignore[assignment]

    def test_returns_on_first_success(self) -> None:
        calls = {"n": 0}

        def fn() -> str:
            calls["n"] += 1
            return "ok"

        self.assertEqual(retry_call(fn, attempts=4), "ok")
        self.assertEqual(calls["n"], 1)
        self.assertEqual(self._delays, [])

    def test_retries_then_succeeds(self) -> None:
        calls = {"n": 0}

        def fn() -> str:
            calls["n"] += 1
            if calls["n"] < 3:
                raise TimeoutError("try again")
            return "done"

        self.assertEqual(retry_call(fn, attempts=5, base_delay=1.0, jitter=0.0), "done")
        self.assertEqual(calls["n"], 3)
        # 指数退避：第1次1.0，第2次2.0
        self.assertEqual(self._delays, [1.0, 2.0])

    def test_gives_up_after_attempts(self) -> None:
        calls = {"n": 0}

        def fn() -> str:
            calls["n"] += 1
            raise urllib.error.URLError("down")

        with self.assertRaises(urllib.error.URLError):
            retry_call(fn, attempts=3, base_delay=1.0, jitter=0.0)
        self.assertEqual(calls["n"], 3)
        self.assertEqual(len(self._delays), 2)  # 重试前 sleep 两次

    def test_fatal_error_not_retried(self) -> None:
        calls = {"n": 0}

        def fn() -> str:
            calls["n"] += 1
            raise urllib.error.HTTPError("u", 401, "unauthorized", {}, None)

        with self.assertRaises(urllib.error.HTTPError):
            retry_call(fn, attempts=5)
        self.assertEqual(calls["n"], 1)
        self.assertEqual(self._delays, [])

    def test_backoff_capped_by_max_delay(self) -> None:
        def fn() -> None:
            raise TimeoutError("x")

        with self.assertRaises(TimeoutError):
            retry_call(fn, attempts=6, base_delay=10.0, max_delay=15.0, jitter=0.0)
        self.assertTrue(all(d <= 15.0 for d in self._delays), self._delays)


if __name__ == "__main__":
    unittest.main()
