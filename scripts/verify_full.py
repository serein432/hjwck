# -*- coding: utf-8 -*-
# 全链路回归验证脚本（用 urllib 避免 requests 双重发送干扰）
# 验证项：/health、播报入队+去重、报警插队、队列状态、语音闭环、指标
import urllib.request
import json

BASE = "http://127.0.0.1:9090"
# 系统 API token（开发默认值，见 settings.server.api_token）
TOKEN = "vsb-dev-token"


def get(path, need_auth=True):
    """GET 请求，返回解析后的 JSON。"""
    headers = {"Authorization": "Bearer " + TOKEN} if need_auth else {}
    req = urllib.request.Request(BASE + path, method="GET", headers=headers)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))


def post(path, body, need_auth=True):
    """POST 请求，返回解析后的 JSON。"""
    data = json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if need_auth:
        headers["Authorization"] = "Bearer " + TOKEN
    req = urllib.request.Request(
        BASE + path, data=data, method="POST", headers=headers,
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    """执行全部验证项并打印结果。"""
    print("=== 1. /health ===")
    print(get("/health", need_auth=False))

    print("\n=== 2. 播报入队（带 dedup_key，首次应成功）===")
    r1 = post("/api/v1/broadcast", {
        "content": "回归验证播报：生产线三号已就绪",
        "source": "verify",
        "priority": 1,
        "dedup_key": "verify-dedup-001",
        "dedup_window": 60,
    })
    print(r1)

    print("\n=== 3. 重复入队（同一 dedup_key，应被去重拦截）===")
    r2 = post("/api/v1/broadcast", {
        "content": "回归验证播报：生产线三号已就绪",
        "source": "verify",
        "priority": 1,
        "dedup_key": "verify-dedup-001",
        "dedup_window": 60,
    })
    print(r2)

    print("\n=== 4. 报警插队（priority=2, broadcast/now）===")
    r3 = post("/api/v1/broadcast/now", {
        "content": "报警：三号产线温度超限，请立即检查",
        "priority": 2,
        "source": "verify-alarm",
    })
    print(r3)

    print("\n=== 5. 队列状态 ===")
    print(get("/api/v1/broadcast/status"))

    print("\n=== 6. 语音闭环（文本输入走 意图→技能→TTS）===")
    r4 = post("/api/v1/voice/text", {"text": "现在几点了"})
    print(r4)

    r5 = post("/api/v1/voice/text", {"text": "帮我查一下订单A1024"})
    print(r5)

    print("\n=== 7. 指标 ===")
    m = get("/api/v1/metrics")
    print(m)

    print("\n=== 验证完成 ===")


if __name__ == "__main__":
    main()
