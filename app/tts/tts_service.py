# -*- coding: utf-8 -*-
"""
TTS 语音合成服务（FR 8.6 / NFR-003,009,011 / 11.3）：
适配器化，混合模式：在线 edge-tts（高质量，需网）→ 离线 Windows SAPI（断网兜底）。
统一 speak(text)：合成 → 播放，音频互斥（同一时刻一路），播报期暂停识别（Q6）。
降级链（NFR-009）：edge-tts 失败 → SAPI；SAPI 也失败 → 仅记日志+回执失败。
"""
import os
import io
import time
import asyncio
import threading

import sounddevice as sd
import soundfile as sf

from app.config.settings import settings, AUDIO_DIR
from app.logging import app_logger as lg

# 播放设备（可配置索引，None=默认）
_device_lock = threading.Lock()
_device_index = None
_playing = False
# 播报期标志：唤醒/ASR 据此暂停识别（回声策略 Q6）
_broad_casting = False


def _set_broad_casting(v):
    global _broad_casting
    _broad_casting = v
    try:
        from app.core.hub import hub
        hub.push("tts_state", {"broadcasting": v})
    except Exception:
        pass


def set_volume(pct):
    """设置音量（0-100），FR-031。"""
    settings.set("voice", "tts", "volume", value=pct)


def get_volume():
    return settings.get("voice", "tts", "volume", default=100)


def is_broadcasting():
    return _broad_casting


def _normalize_audio(audio_bytes, src_fmt, src_sr):
    """把任意格式音频转成 16bit/16k 单声道 WAV（供 sounddevice 播放）。"""
    try:
        data, sr = sf.read(io.BytesIO(audio_bytes), dtype="float32")
    except Exception:
        # MP3 等 soundfile 不支持，尝试用 edge-tts 直接给 mp3 → 这里退化为 SAPI
        return None, None
    if data.ndim > 1:
        data = data.mean(axis=1)
    # 简单重采样到 16k
    if sr != 16000:
        n_out = int(len(data) * 16000 / sr)
        idx = (numpy_arange(n_out) * sr / 16000).astype(int)
        idx = idx[idx < len(data)]
        data = data[idx]
    pcm16 = (data * 32767).astype("int16")
    wav = sf.to_wav(pcm16, 16000)
    return wav, 16000


def _arange(n):
    # 轻量 numpy 替代（避免硬依赖，numpy 随 soundfile 已装）
    import numpy as np
    return np.arange(n)


numpy_arange = _arange


class TTSService:
    def __init__(self):
        self._edge_lock = threading.Lock()
        self._edge_ready = False

    # ---------- 在线 edge-tts ----------
    async def _edge_tts(self, text):
        import edge_tts
        voice = settings.get("voice", "tts", "voice", default="zh-CN-XiaoxiaoNeural")
        speed = settings.get("voice", "tts", "speed", default=1.0)
        communicate = edge_tts.Communicate(text, voice, rate=f"{int((speed-1)*100)}%")
        buf = io.BytesIO()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                buf.write(chunk["data"])
        return buf.getvalue()  # mp3

    # ---------- 合成（带降级） ----------
    def synthesize(self, text, out_path=None):
        """合成文本为音频，返回 (audio_bytes, sr, fmt)。失败返回 (None,None,None)。"""
        lg.inc("tts_count")
        backend = settings.get("voice", "tts", "backend", default="auto")
        online = settings.get("voice", "tts", "online", default="edge-tts")
        # auto：有网用在线
        if backend == "auto":
            backend = online if _network() else settings.get("voice", "tts", "offline", default="sapi")
        elif backend == "online":
            backend = online
        elif backend == "offline":
            backend = settings.get("voice", "tts", "offline", default="sapi")

        try:
            if backend == "edge-tts":
                t0 = time.time()
                mp3 = asyncio.run(self._edge_tts(text))
                if mp3 and len(mp3) > 0:
                    lg.inc("tts_success")
                    lg.log("info", "tts", "edge-tts 合成成功", ms=int((time.time()-t0)*1000), bytes=len(mp3))
                    if out_path:
                        with open(out_path, "wb") as f:
                            f.write(mp3)
                    return mp3, 24000, "mp3"
                lg.log("warn", "tts", "edge-tts 返回空，降级 SAPI")
            # SAPI 离线（直接 speak，无音频文件；返回 None 表示走实时 speak）
            lg.log("info", "tts", "使用 SAPI 离线合成")
            return None, None, "sapi"
        except Exception as e:
            lg.log("error", "tts", f"{backend} 合成失败，降级 SAPI", err=str(e))
            return None, None, "sapi"

    # ---------- 播放 ----------
    def _play_bytes(self, audio_bytes, sr, fmt):
        """播放音频字节。"""
        wav, new_sr = _normalize_audio(audio_bytes, fmt, sr)
        if wav is None:
            # mp3 soundfile 可能读不了 → 落盘重试
            return False
        data, _ = sf.read(io.BytesIO(wav), dtype="float32")
        vol = get_volume() / 100.0
        data = data * vol
        sd.play(data, new_sr)
        sd.wait()
        return True

    def speak(self, text, priority=0):
        """
        核心入口：合成并播放。音频互斥（排队），播报期暂停识别。
        返回 True=播报成功。在队列 worker 线程调用（非事件循环内）。
        """
        global _playing  # 模块级互斥标志，赋值需声明 global
        with _device_lock:
            if _playing:
                # 已有播放在进行，等待其结束（简单自旋）
                while _playing:
                    time.sleep(0.05)
            _playing = True
        _set_broad_casting(True)
        try:
            audio, sr, fmt = self.synthesize(text)
            if fmt == "sapi" or audio is None:
                # SAPI 离线：直接系统朗读（阻塞直到播完）
                return self._sapi_speak(text)
            return self._play_bytes(audio, sr, fmt)
        except Exception as e:
            lg.log("error", "tts", "播放失败", err=str(e))
            return False
        finally:
            with _device_lock:
                _playing = False
            _set_broad_casting(False)

    def _sapi_speak(self, text):
        """Windows SAPI 离线朗读（阻塞）。音量随配置。"""
        try:
            import pythoncom
            import win32com.client
            pythoncom.CoInitialize()
            spv = win32com.client.Dispatch("SAPI.SPVoice")
            vol = get_volume()
            spv.Volume = vol  # 0-100
            # 阻塞式朗读（SWP 0=阻塞）
            spv.Speak(text, 0)
            lg.inc("tts_success")
            return True
        except Exception as e:
            lg.log("error", "tts", "SAPI 朗读失败", err=str(e))
            return False
        finally:
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass


def _network(timeout=1.5):
    """快速探测外网是否可用（决定 online/offline）。"""
    import socket
    try:
        socket.setdefaulttimeout(timeout)
        socket.create_connection(("239.255.255.255", 53), 1.0).close()
        return True
    except Exception:
        try:
            socket.create_connection(("www.microsoft.com", 443), timeout).close()
            return True
        except Exception:
            return False


# 全局单例
tts_service = TTSService()
