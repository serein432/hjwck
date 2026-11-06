# -*- coding: utf-8 -*-
"""
桌面 App 启动器（pywebview 壳 + 内嵌后端）。
- 在独立线程内启动 uvicorn 后端（默认 127.0.0.1:9090）
- 用 pywebview 加载 app/desktop/index.html（8 大功能界面）
- 单进程运行，无需单独启动后端；关闭窗口自动终止后端
用法：python -m app.desktop.app_desktop  或  python app/desktop/app_desktop.py
"""
import os
import sys
import time
import threading

import uvicorn

# 保证可独立运行（python app/desktop/app_desktop.py）时能导入 app 包
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from app.config.settings import settings  # noqa: E402
from app.logging import app_logger as lg  # noqa: E402

# 桌面界面 HTML 路径
HTML_PATH = os.path.join(os.path.dirname(__file__), "index.html")


def _start_backend(host, port):
    """在独立线程内启动 uvicorn 后端。"""
    config = uvicorn.Config(
        "app.main:app",
        host=host,
        port=port,
        log_level="warning",
        lifespan="on",  # 确保 startup 事件（触发源初始化）执行
    )
    server = uvicorn.Server(config)
    server.run()  # 阻塞，占用当前线程


def _wait_health(host, port, timeout=30):
    """轮询 /health 直到后端就绪，返回是否成功。"""
    import urllib.request
    url = "http://%s:%d/health" % (host, port)
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.4)
    return False


def main():
    host = settings.get("server", "host", default="127.0.0.1")
    port = int(settings.get("server", "port", default=9090))

    # 1) 内嵌启动后端（独立线程）
    backend = threading.Thread(target=_start_backend, args=(host, port), daemon=True)
    backend.start()
    lg.log("info", "desktop", "后端线程已启动", host=host, port=port)

    # 2) 等待后端就绪
    if not _wait_health(host, port, timeout=30):
        lg.log("error", "desktop", "后端未就绪", host=host, port=port)
        print("后端启动超时，请检查端口 %d 是否被占用。" % port)
        return

    # 3) 打开桌面窗口（pywebview 通过 HTTP 加载界面）
    # 注意：不用本地文件路径——pywebview 拼 file:// URI 时不编码中文路径
    #       （python文件夹），导致 WebView 解析失败、界面空白。
    #       改为后端 HTTP 提供 /app/，主机名是纯 ASCII，稳定。
    import webview
    url = "http://%s:%d/app/" % (host, port)
    webview.create_window(
        "智能语音播报系统",
        url,
        width=1280,
        height=800,
        min_size=(1000, 640),
        background_color="#0f1420",
    )
    webview.start()
    # webview.start() 返回 = 窗口已关闭 → 进程退出，daemon 后端线程随之终止


if __name__ == "__main__":
    main()
