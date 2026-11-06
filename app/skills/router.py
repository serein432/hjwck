# -*- coding: utf-8 -*-
"""
技能路由与业务执行（FR-005/006 / 8.5）：
按意图路由到对应技能并执行，返回结果文本。技能插件化注册（builtin.*）。
内置技能：时间 / 天气（演示数据源）/ 订单（演示数据源）/ 问候。
"""
import time
import random
import json

from app.db.database import db
from app.logging import app_logger as lg

WEEKDAY_CN = "一二三四五六日"


def _now_str():
    t = time.localtime()
    return {
        "year": t.tm_year, "month": t.tm_mon, "day": t.tm_mday,
        "weekday": WEEKDAY_CN[t.tm_wday], "hour": t.tm_hour, "minute": t.tm_min,
    }


# ---------- 内置技能实现 ----------
def skill_time(intent):
    """查询时间（FR-010）。"""
    n = _now_str()
    return f"现在是{n['year']}年{n['month']}月{n['day']}日，星期{n['weekday']}，{n['hour']}点{n['minute']}分。"


def skill_weather(intent):
    """查询天气（FR-009），演示数据源（Q3：无真实业务系统时用内置模拟）。"""
    city = intent.get("slots", {}).get("city", "北京")
    date = intent.get("slots", {}).get("date", "今天")
    # 演示天气数据（可替换为真实 API）
    w = random.choice([
        {"phenomenon": "晴", "low": 18, "high": 28, "wind": "东北风2级"},
        {"phenomenon": "多云", "low": 16, "high": 25, "wind": "东南风3级"},
        {"phenomenon": "小雨", "low": 15, "high": 22, "wind": "北风2级"},
    ])
    n = _now_str()
    return f"{city}{date}{w['phenomenon']}，气温{w['low']}到{w['high']}度，{w['wind']}。"


def skill_order(intent):
    """查询订单（FR-011），演示数据源。"""
    oid = intent.get("slots", {}).get("order_id", "A1024")
    # 演示订单
    return f"订单{oid}：商品机械密封件×2，金额580元，状态已确认，预计明天发货。"


def skill_greet(intent):
    """问候闲聊。"""
    return "您好，我是智能语音助手小帮手。可以问我时间、天气、订单，也可以说播报、暂停等指令。"


# 技能注册表（插件化，handler 为 "module.func"）
_REGISTRY = {
    "query_time": skill_time,
    "query_weather": skill_weather,
    "query_order": skill_order,
    "greet": skill_greet,
}


def list_skills():
    """列出已注册技能（供界面技能管理页）。"""
    # 从 skill_config 表读 + 注册表
    rows = db.query("SELECT * FROM skill_config ORDER BY skill_id")
    out = []
    for r in rows:
        out.append({
            "skill_id": r["skill_id"], "name": r["name"], "intent": r["intent"],
            "handler": r["handler"], "enabled": bool(r["enabled"]),
            "registered": r["intent"] in _REGISTRY,
        })
    return out


def set_skill_enabled(skill_id, enabled):
    db.execute("UPDATE skill_config SET enabled=? WHERE skill_id=?", (1 if enabled else 0, skill_id))


def route(intent):
    """
    技能路由：意图 → 结果文本。
    返回 (text, skill_id)。未识别/技能停用 → 提示文本。
    """
    intent_name = intent.get("intent", "fallback")
    # 检查技能是否启用
    row = db.query_one("SELECT enabled FROM skill_config WHERE intent=?", (intent_name,))
    if row and not row["enabled"]:
        return "该技能已停用，请先到技能管理页启用。", None
    handler = _REGISTRY.get(intent_name)
    if handler is None:
        return "抱歉，我暂时还不会这个。你可以问我时间、天气、订单，或者试试其他说法。", None
    try:
        text = handler(intent)
        lg.log("info", "skill", "技能执行成功", intent=intent_name)
        return text, intent_name
    except Exception as e:
        lg.log("error", "skill", "技能执行异常", intent=intent_name, err=str(e))
        return "抱歉，刚才处理出错了，请稍后再试。", None
