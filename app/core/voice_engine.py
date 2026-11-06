# -*- coding: utf-8 -*-
"""
语音交互引擎（M3 核心 / FR-001~007 / 4.1 交互式语音流程）：
编排 唤醒→ASR→意图理解→技能路由→TTS→播放 闭环，支持多轮追问补参（FR-006）。
两种入口：
  1) 麦克风闭环：唤醒词命中→录音→ASR→意图→技能→TTS→播放（需真实麦克风+Vosk模型）
  2) 文本注入：App 界面输入文字直接走 ASR 后链路（无硬件也能演示闭环，便于验收）
所有关键环节 push 到 hub 供界面实时监控。
"""
import time
import threading
import uuid

from app.config.settings import settings
from app.logging import app_logger as lg
from app.audio import capture
from app.wakeword.detector import detector
from app.asr.asr_service import asr_service
from app.nlu import intent as nlu
from app.skills import router
from app.queue.broadcast_queue import queue
from app.core.hub import hub


class VoiceSession:
    """多轮对话会话状态（FR-006 / 8.12）。"""

    def __init__(self, session_id=None, ttl=30):
        self.session_id = session_id or uuid.uuid4().hex[:12]
        self.intent = None
        self.slots = {}
        self.history = []      # [(text, intent, result)]
        self.created = time.time()
        self.ttl = ttl

    def update(self, text, intent_obj, result):
        self.history.append({"text": text, "intent": intent_obj["intent"], "result": result})
        if intent_obj["intent"] != "fallback":
            self.intent = intent_obj["intent"]
            self.slots = dict(intent_obj["slots"])

    def context(self):
        return {"intent": self.intent, "slots": dict(self.slots)}

    def expired(self):
        return (time.time() - self.created) > self.ttl


class VoiceEngine:
    def __init__(self):
        self._session = None
        self._lock = threading.Lock()
        self._listening = False

    # ---------- 会话管理 ----------
    def _get_session(self):
        with self._lock:
            if self._session is None or self._session.expired():
                self._session = VoiceSession()
            return self._session

    def reset_session(self):
        with self._lock:
            self._session = None
        lg.log("info", "voice", "会话已重置")

    # ---------- 文本注入入口（界面/测试用） ----------
    def handle_text(self, text):
        """
        文本注入：走 意图理解→技能路由→播报，返回结果文本。
        支持多轮：缺槽位时返回追问话术并保持会话。
        """
        text = (text or "").strip()
        if not text:
            return ""
        session = self._get_session()
        intent_obj = nlu.understand(text, session.context())
        lg.log("info", "voice", "文本交互", text=text, intent=intent_obj["intent"], slots=intent_obj["slots"])

        # 推识别+意图事件
        hub.push("asr", {"text": text, "confidence": intent_obj["confidence"], "source": "text"})
        hub.push("nlu", {"intent": intent_obj["intent"], "slots": intent_obj["slots"],
                         "missing": intent_obj["missing"], "session": session.session_id})

        # 缺槽位 → 追问补参（FR-004）
        if intent_obj["missing"]:
            missing = intent_obj["missing"][0]
            reply = nlu.CLARIFY_TPL.get(missing, "请补充相关信息。")
            session.update(text, intent_obj, reply)  # 保留部分槽位
            hub.push("result", {"text": reply, "intent": intent_obj["intent"], "skill": None, "session": session.session_id})
            self._enqueue(reply)
            return reply

        # 技能路由
        result_text, skill_id = router.route(intent_obj)
        session.update(text, intent_obj, result_text)
        hub.push("result", {"text": result_text, "intent": intent_obj["intent"], "skill": skill_id,
                            "session": session.session_id})
        # 结果进入播报队列（交互结果通常高优先级）
        self._enqueue(result_text, priority=1)
        return result_text

    def _enqueue(self, text, priority=1):
        """把交互结果/追问送入播报队列。"""
        queue.enqueue(text, source="voice_interaction", priority=priority, type_="interaction")

    # ---------- 麦克风闭环 ----------
    def start_listening(self):
        """开始监听（唤醒词持续检测，energy 模式）。"""
        detector.set_active(True)
        self._listening = True
        lg.log("info", "voice", "进入监听状态")

    def stop_listening(self):
        self._listening = False
        detector.set_active(False)
        lg.log("info", "voice", "退出监听状态")

    def process_recording(self, timeout=5.0):
        """
        处理一次录音（唤醒后调用）：录音→ASR→意图→技能→播报。
        返回结果文本。timeout 秒内无输入则超时。
        """
        if capture.recording:
            capture.stop()
        else:
            capture.start()
            # 等待录音（VAD 简化：固定采集到能量回落或超时）
            t0 = time.time()
            while capture.recording and (time.time() - t0) < timeout:
                time.sleep(0.1)
        wav, sr = capture.stop()
        # ASR
        asr = asr_service.transcribe(wav, sr)
        text, conf = asr
        hub.push("asr", {"text": text, "confidence": conf, "source": "mic"})
        lg.log("info", "voice", "ASR 结果", text=text, conf=conf)
        if not text:
            return ""
        # 后续与文本注入一致
        session = self._get_session()
        intent_obj = nlu.understand(text, session.context())
        hub.push("nlu", {"intent": intent_obj["intent"], "slots": intent_obj["slots"],
                         "missing": intent_obj["missing"], "session": session.session_id})
        if intent_obj["missing"]:
            reply = nlu.CLARIFY_TPL.get(intent_obj["missing"][0], "请补充相关信息。")
            session.update(text, intent_obj, reply)
            self._enqueue(reply)
            return reply
        result_text, skill_id = router.route(intent_obj)
        session.update(text, intent_obj, result_text)
        hub.push("result", {"text": result_text, "intent": intent_obj["intent"], "skill": skill_id,
                            "session": session.session_id})
        self._enqueue(result_text, priority=1)
        return result_text

    # ---------- 状态 ----------
    def status(self):
        s = self._session
        return {
            "listening": self._listening,
            "wake": detector.state(),
            "asr_ready": asr_service.model_ready(),
            "session": {
                "id": s.session_id if s else None,
                "intent": s.intent if s else None,
                "slots": s.slots if s else {},
                "turns": len(s.history) if s else 0,
            } if s else None,
            "queue": queue.state(),
        }


# 全局单例
engine = VoiceEngine()
