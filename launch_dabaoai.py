from __future__ import annotations

import threading
import time
import urllib.request
import webbrowser
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parent
HOST = "127.0.0.1"
PORT = 7861
URL = f"http://{HOST}:{PORT}"
APP_NAME = "DaobaoAI-DY 电视剧全自动智能剪辑工具"


def healthy() -> bool:
    try:
        with urllib.request.urlopen(f"{URL}/api/health", timeout=1) as response:
            return response.status == 200
    except Exception:
        return False


def open_when_ready() -> None:
    for _ in range(120):
        if healthy():
            webbrowser.open(URL)
            print(f"\n{APP_NAME} 已启动：{URL}")
            return
        time.sleep(0.5)
    print(f"{APP_NAME} 启动超时，请查看上方错误信息。")


def main() -> None:
    if healthy():
        webbrowser.open(URL)
        print(f"{APP_NAME} 已经在运行：{URL}")
        return
    try:
        from backend.concurrency import detect_optimal_concurrency
        optimal = detect_optimal_concurrency()
        print(f"[启动] 检测到最优并发数: {optimal}")
    except Exception as exc:
        print(f"[启动] 并发检测跳过: {exc}")
    threading.Thread(target=open_when_ready, daemon=True).start()
    import uvicorn
    os.chdir(ROOT)
    uvicorn.run("app:app", host=HOST, port=PORT, log_level="info")


if __name__ == "__main__":
    main()
