# -*- coding: utf-8 -*-
"""
实时事件中枢（hub）：后端关键事件 → WebSocket → App 界面实时监控。
事件类型：wake 唤醒状态 / voice 录音波形 / asr 识别文本 / nlu 意图 /
skill 技能结果 / tts_state 播报状态 / queue_event 队列事件 / log 日志 / status 状态。
线程安全，断连自动清理。
"""
import json
import asyncio
import threading
from collections import deque


class Hub:
    def __init__(self):
        self._clients = set()          # 活动 WebSocket
        self._loop = None              # 事件循环（用于线程→协程投递）
        self._lock = threading.Lock()
        # 最近事件环形缓冲（供 App 重连后补拉对话记录/日志）
        self._recent = deque(maxlen=200)

    def bind_loop(self, loop):
        self._loop = loop

    def add_client(self, ws):
        with self._lock:
            self._clients.add(ws)

    def remove_client(self, ws):
        with self._lock:
            self._clients.discard(ws)

    def push(self, event, data=None):
        """线程安全推送事件给所有客户端。"""
        msg = json.dumps({"event": event, "data": data or {}}, ensure_ascii=False)
        self._recent.append({"event": event, "data": data or {}})
        if not self._loop or self._loop.is_closed():
            return
        with self._lock:
            clients = list(self._clients)
        for ws in clients:
            try:
                asyncio.run_coroutine_threadsafe(self._send(ws, msg), self._loop)
            except Exception:
                pass

    async def _send(self, ws, msg):
        try:
            await ws.send_text(msg)
        except Exception:
            self.remove_client(ws)

    def recent(self, n=100):
        """最近 n 条事件（App 重连补拉）。"""
        return list(self._recent)[-n:]

    def client_count(self):
        with self._lock:
            return len(self._clients)


# 全局单例
hub = Hub()
