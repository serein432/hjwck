# -*- coding: utf-8 -*-
"""
日志与审计（FR-036~039）：分级日志 + 轮转 + 指标统计。
- run.log：运行日志（DEBUG/INFO/WARN/ERROR）按大小轮转
- audit.log：播报审计日志（内容/来源/时间/优先级/结果）
- 指标：每日播报量、识别成功率、TTS调用量、队列积压、报警时效
"""
import os
import json
import time
import logging
import threading
from logging.handlers import RotatingFileHandler

from app.config.settings import LOG_DIR, settings

_run_lock = threading.Lock()
_metrics_lock = threading.Lock()

# 内存指标计数器
_metrics = {
    "broadcast_count": 0,      # 播报量
    "broadcast_ok": 0,         # 播报成功
    "broadcast_fail": 0,       # 播报失败
    "asr_count": 0,            # ASR 调用量
    "asr_success": 0,          # ASR 成功
    "tts_count": 0,            # TTS 调用量
    "tts_success": 0,          # TTS 成功
    "alert_total": 0,          # 报警总数
    "alert_ack_ms": [],        # 报警处理时效(ms)
}


def _build(name, level, max_bytes=2 * 1024 * 1024, backups=5):
    """构建带轮转的文件 logger。"""
    lg = logging.getLogger(name)
    if lg.handlers:
        return lg
    lg.setLevel(level)
    fh = RotatingFileHandler(
        os.path.join(LOG_DIR, name + ".log"),
        maxBytes=max_bytes, backupCount=backups, encoding="utf-8"
    )
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] [%(name)s] %(message)s")
    fh.setFormatter(fmt)
    lg.addHandler(fh)
    lg.propagate = False
    return lg


# 运行日志 + 审计日志
logger = _build("vsb", logging.INFO)
audit = _build("vsb-audit", logging.INFO)


def log(level, module, msg, **ctx):
    """统一运行日志入口，结构化输出（含模块与上下文 JSON）。"""
    line = f"[{module}] {msg}"
    if ctx:
        line += " " + json.dumps(ctx, ensure_ascii=False)
    getattr(logger, level)(line)


def audit_log(task_id, content, source, priority, result):
    """播报审计日志（FR-037）。"""
    audit.info(json.dumps({
        "task_id": task_id, "content": content, "source": source,
        "priority": priority, "result": result,
        "time": time.strftime("%Y-%m-%d %H:%M:%S")
    }, ensure_ascii=False))


def inc(key, n=1):
    """累加指标计数。"""
    with _metrics_lock:
        _metrics[key] = _metrics.get(key, 0) + n


def metric_alert_time(ms):
    """记录报警处理时效。"""
    with _metrics_lock:
        _metrics["alert_total"] += 1
        _metrics["alert_ack_ms"].append(ms)


def snapshot():
    """返回指标快照（供监控看板）。"""
    with _metrics_lock:
        asr_rate = round(_metrics["asr_success"] / _metrics["asr_count"] * 100, 1) if _metrics["asr_count"] else None
        avg_alert = round(sum(_metrics["alert_ack_ms"]) / len(_metrics["alert_ack_ms"])) if _metrics["alert_ack_ms"] else None
        return {
            "broadcast_count": _metrics["broadcast_count"],
            "broadcast_ok": _metrics["broadcast_ok"],
            "broadcast_fail": _metrics["broadcast_fail"],
            "asr_count": _metrics["asr_count"],
            "asr_success": _metrics["asr_success"],
            "asr_success_rate": asr_rate,
            "tts_count": _metrics["tts_count"],
            "tts_success": _metrics["tts_success"],
            "alert_total": _metrics["alert_total"],
            "alert_avg_ms": avg_alert,
        }


def reset_metrics():
    with _metrics_lock:
        for k in _metrics:
            _metrics[k] = 0 if isinstance(_metrics[k], int) else []
