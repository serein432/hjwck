# -*- coding: utf-8 -*-
"""
唤醒词模块（FR-001 / 8.2 / Q7）：
- 默认 energy 模式：能量门槛 + 关键词（对已识别文本做匹配，无外部 key 即可跑）
- 可选 porcupine 引擎（需 AccessKey，配置后启用）
设计：唤醒检测跑在音频帧上，命中后触发录音。本实现采用"录音→ASR→匹配唤醒词"
的简化能量唤醒（无独立唤醒引擎时最稳），并保留 Porcupine 适配接口。
"""
import re
import threading

from app.config.settings import settings
from app.logging import app_logger as lg


class WakeWordDetector:
    def __init__(self):
        self._active = False
        self._lock = threading.Lock()
        self._porcupine = None

    # ---------- 配置 ----------
    def word(self):
        return settings.get("voice", "wake_word", default="小帮手")

    def set_word(self, w):
        settings.set("voice", "wake_word", value=w)
        lg.log("info", "wakeword", "唤醒词更新", word=w)

    def mode(self):
        return settings.get("voice", "wake_mode", default="energy")

    # ---------- Porcupine 适配（可选，需 key） ----------
    def _init_porcupine(self):
        """尝试初始化 Porcupine（需 pvporcupine 包 + access key）。失败回退 energy。"""
        if self._porcupine is not None:
            return self._porcupine
        try:
            import pvporcupine
            key = settings.get("voice", "porcupine_key", default="")
            if not key:
                return None
            self._porcupine = pvporcupine.create(access_key=key, keywords=[self.word()])
            lg.log("info", "wakeword", "Porcupine 引擎已启用")
            return self._porcupine
        except Exception as e:
            lg.log("warn", "wakeword", "Porcupine 不可用，回退能量+关键词", err=str(e))
            self._porcupine = None
            return None

    # ---------- 检测 ----------
    def detect_audio(self, pcm16, sr=16000):
        """
        音频帧级唤醒检测（Porcupine 路径）。返回 True=命中。
        pcm16: int16 numpy 数组。
        """
        pp = self._init_porcupine()
        if pp is not None:
            try:
                import numpy as np
                return pp.process(pcm16.reshape(-1).tolist()) >= 0
            except Exception:
                return False
        return False

    def match_text(self, text):
        """文本级唤醒词匹配（energy 模式主路径）：识别文本含唤醒词即命中。"""
        if not text:
            return False
        w = self.word()
        # 去空格匹配（"小 帮手" 也算）
        return w.replace(" ", "") in text.replace(" ", "")

    # ---------- 状态 ----------
    @property
    def active(self):
        with self._lock:
            return self._active

    def set_active(self, v):
        with self._lock:
            self._active = v
        try:
            from app.core.hub import hub
            hub.push("wake", {"active": v, "word": self.word(), "mode": self.mode()})
        except Exception:
            pass

    def state(self):
        return {
            "active": self.active,
            "word": self.word(),
            "mode": self.mode(),
            "porcupine": self._porcupine is not None,
        }


# 全局单例
detector = WakeWordDetector()
