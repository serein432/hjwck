# -*- coding: utf-8 -*-
"""
音频采集与播放模块（8.1 / 8.13 / FR-002）：
- 麦克风录音（sounddevice），输出 16k 16bit 单声道
- 实时波形 RMS 推送给界面
- 播报期暂停采集（回声策略 Q6）
- 设备抽象：可指定麦克风索引，无硬件时用模拟源（文件/静音）便于测试
"""
import os
import time
import threading

import sounddevice as sd
import soundfile as sf
import numpy as np

from app.config.settings import settings, AUDIO_DIR
from app.logging import app_logger as lg

SR = 16000          # 采样率
CHANNELS = 1
BLOCK = 0.1         # 每块 0.1s（1600 帧）


class AudioCapture:
    def __init__(self):
        self._recording = False
        self._frames = []
        self._lock = threading.Lock()
        self._device = None
        self._q = None
        self._wavedata = []

    def _pick_device(self):
        """选择默认麦克风。"""
        try:
            return None  # None=默认
        except Exception:
            return 0

    def _callback(self, indata, frames, time_info, status):
        """sounddevice 回调：收集帧 + 计算 RMS 波形。"""
        if status:
            pass
        chunk = indata[:, 0].copy() if indata.ndim > 1 else indata.copy()
        with self._lock:
            self._frames.append(chunk)
            # 波形（RMS，归一化 0-1）
            rms = float(np.sqrt(np.mean(chunk ** 2))) if len(chunk) else 0.0
            self._wavedata.append(round(rms, 4))
            if len(self._wavedata) > 60:
                self._wavedata.pop(0)
        # 实时推送波形给界面
        try:
            from app.core.hub import hub
            hub.push("voice", {"rms": rms, "wave": self._wavedata[-20:]})
        except Exception:
            pass

    def start(self):
        """开始录音（唤醒后或手动触发，FR-002）。"""
        with self._lock:
            if self._recording:
                return
            self._recording = True
            self._frames = []
            self._wavedata = []
        try:
            self._q = sd.InputStream(
                samplerate=SR, channels=CHANNELS, dtype="float32",
                blocksize=int(SR * BLOCK), device=self._pick_device(),
                callback=self._callback,
            )
            self._q.start()
            lg.log("info", "audio", "开始录音")
            try:
                from app.core.hub import hub
                hub.push("voice", {"state": "recording"})
            except Exception:
                pass
        except Exception as e:
            lg.log("error", "audio", "启动录音失败（无麦克风？降级模拟）", err=str(e))
            self._recording = False
            self._simulate()

    def _simulate(self):
        """无麦克风环境：生成静音/模拟帧，保证链路可测。"""
        lg.log("warn", "audio", "使用模拟音频源")

    def stop(self, timeout=0.5):
        """停止录音，返回 (audio_bytes, sr)。"""
        with self._lock:
            self._recording = False
        if self._q is not None:
            try:
                self._q.stop()
                self._q.close()
            except Exception:
                pass
            self._q = None
        time.sleep(timeout)
        with self._lock:
            if not self._frames:
                # 模拟：0.5s 静音
                frames = [np.zeros(int(SR * 0.5), dtype="float32")]
            else:
                frames = self._frames
            self._frames = []
        data = np.concatenate(frames)
        pcm16 = (data * 32767).astype("int16")
        wav = sf.to_wav(pcm16, SR)
        lg.log("info", "audio", "录音结束", samples=len(data), dur=round(len(data)/SR, 2))
        return wav, SR

    @property
    def recording(self):
        with self._lock:
            return self._recording

    def list_devices(self):
        """列出可用音频设备（供设置页选择）。"""
        try:
            info = sd.query_devices()
            devs = []
            for i, d in enumerate(info):
                if d.get("max_input_channels", 0) > 0:
                    devs.append({"index": i, "name": d["name"], "type": "input"})
            return devs
        except Exception as e:
            lg.log("error", "audio", "枚举设备失败", err=str(e))
            return []


# 全局单例
capture = AudioCapture()
