# -*- coding: utf-8 -*-
"""
ASR 语音识别服务（FR-003 / 8.3 / NFR-011）：
适配器化，混合模式：离线 Vosk（中文小模型，CPU 可跑）→ 在线（可接讯飞/Azure，占位）。
Vosk 模型首次使用需下载（scripts/download_models.py）。模型缺失时降级：
返回空文本并记日志，链路不中断（NFR-009）。
"""
import os
import json
import threading

import numpy as np

from app.config.settings import settings, MODEL_DIR
from app.logging import app_logger as lg

VOSK_MODEL_DIR = os.path.join(MODEL_DIR, "vosk-cn")


class ASRService:
    def __init__(self):
        self._rec = None
        self._lock = threading.Lock()
        self._model_ok = False

    # ---------- 模型加载（Vosk 离线） ----------
    def _ensure_vosk(self):
        """懒加载 Vosk 识别器。模型未下载则返回 False。"""
        if self._rec is not None:
            return True
        if not os.path.isdir(VOSK_MODEL_DIR):
            return False
        try:
            from vosk import Model, KaldiRecognizer
            import soundfile as sf
            self._model = Model(VOSK_MODEL_DIR)
            self._model_ok = True
            lg.log("info", "asr", "Vosk 离线模型加载成功")
            return True
        except Exception as e:
            lg.log("error", "asr", "Vosk 模型加载失败", err=str(e))
            return False

    def _new_rec(self, sr=16000):
        from vosk import KaldiRecognizer
        return KaldiRecognizer(self._model, sr)

    # ---------- 在线 ASR（占位，可接讯飞/Azure） ----------
    def _online_asr(self, wav_bytes, sr):
        """在线 ASR 占位。默认返回 None，由上层降级离线。"""
        online = settings.get("voice", "asr", "online", default="none")
        if online in ("none", "", None):
            return None
        # 预留接入点：此处调用在线 API 并返回 (text, conf)
        return None

    # ---------- 识别 ----------
    def transcribe(self, wav_bytes, sr=16000):
        """
        识别音频 → (text, confidence)。
        混合模式：有网优先在线（当前占位返回 None），否则离线 Vosk。
        """
        lg.inc("asr_count")
        backend = settings.get("voice", "asr", "backend", default="auto")
        # auto：先试在线（占位 None），再离线
        text = None
        if backend in ("auto", "online"):
            try:
                r = self._online_asr(wav_bytes, sr)
                if r:
                    text, conf = r
            except Exception as e:
                lg.log("warn", "asr", "在线 ASR 失败，降级离线", err=str(e))
        # 离线 Vosk
        if text is None:
            text, conf = self._offline(wav_bytes, sr)
        if text:
            lg.inc("asr_success")
        return text or "", conf

    def _offline(self, wav_bytes, sr=16000):
        """离线 Vosk 识别。"""
        if not self._ensure_vosk():
            lg.log("warn", "asr", "Vosk 模型未就绪，无法离线识别（请运行 scripts/download_models.py）")
            return "", 0.0
        try:
            import io
            import soundfile as sf
            data, sr2 = sf.read(io.BytesIO(wav_bytes), dtype="int16")
            if data.ndim > 1:
                data = data.mean(axis=1)
            rec = self._new_rec(sr2)
            chunk = 8000
            parts = []
            for i in range(0, len(data), chunk):
                buf = (data[i:i + chunk] * 256).astype("int16").tobytes()
                if rec.AcceptWaveform(buf):
                    j = json.loads(rec.Result())
                    if j.get("text"):
                        parts.append(j["text"])
            j = json.loads(rec.FinalResult())
            if j.get("text"):
                parts.append(j["text"])
            text = "".join(parts).strip()
            conf = 0.85 if text else 0.0
            return text, conf
        except Exception as e:
            lg.log("error", "asr", "Vosk 识别异常", err=str(e))
            return "", 0.0

    def model_ready(self):
        """模型是否就绪（供健康检查/界面提示）。"""
        return self._ensure_vosk()


# 全局单例
asr_service = ASRService()
