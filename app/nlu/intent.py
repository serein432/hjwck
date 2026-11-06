# -*- coding: utf-8 -*-
"""
意图理解模块（FR-004 / 8.4）：
规则 + 词典的轻量 NLU：意图分类 + 槽位抽取，缺槽位返回待追问字段（多轮补参）。
意图域聚焦系统技能：query_time / query_weather / query_order / greet / fallback。
规则可配置（INTENT_RULES 字典），新增意图只需加规则。
"""
import re

# 城市词典（天气槽位用，可扩展）
_CITIES = [
    "北京", "上海", "广州", "深圳", "杭州", "成都", "武汉", "西安", "南京",
    "重庆", "天津", "苏州", "长沙", "郑州", "青岛", "大连", "厦门", "福州",
    "合肥", "沈阳", "济南", "宁波", "无锡", "东莞", "佛山", "珠海",
]

# 时间槽位
_TIME_WORDS = {
    "今天": "today", "明天": "tomorrow", "后天": "after_tomorrow",
    "昨天": "yesterday", "现在": "now", "当前": "now",
}

# 意图规则：(意图, 关键词列表, 必需槽位)
INTENT_RULES = [
    ("query_weather", ["天气", "气温", "温度", "下雨", "风力", "天气怎么样"], ["city"]),
    ("query_order", ["订单", "单号", "新订单", "订单号", "查订单", "有没有订单"], ["order_id"]),
    ("query_time", ["时间", "几点", "现在几点", "报时", "日期", "星期", "几号"], []),
    ("greet", ["你好", "您好", "嗨", "哈喽", "hello", "hi", "在吗", "早上好", "下午好", "晚上好"], []),
]


def _extract_city(text):
    for c in _CITIES:
        if c in text:
            return c
    return None


def _extract_date(text):
    for w, v in _TIME_WORDS.items():
        if w in text:
            return v
    return None


def _extract_order(text):
    # 订单号：字母+数字 / 纯数字串
    m = re.search(r"([A-Za-z]+\d+|\d{4,})", text)
    return m.group(1) if m else None


def understand(text, context=None):
    """
    意图理解。返回 Intent 字典：
    {intent, slots, missing, confidence}
    context: 上一轮意图/槽位（多轮补参用）
    """
    if not text:
        return {"intent": "fallback", "slots": {}, "missing": [], "confidence": 0.0}
    t = text.strip()

    # 逐条规则匹配（按优先级：天气/订单/时间/问候）
    for intent, kws, req_slots in INTENT_RULES:
        if any(kw in t for kw in kws):
            slots = {}
            # 抽槽位
            city = _extract_city(t)
            if city:
                slots["city"] = city
            date = _extract_date(t)
            if date:
                slots["date"] = date
            order = _extract_order(t)
            if order:
                slots["order_id"] = order
            # 多轮补参：继承上轮槽位
            if context and isinstance(context, dict):
                for k, v in context.get("slots", {}).items():
                    slots.setdefault(k, v)
            missing = [s for s in req_slots if s not in slots]
            return {
                "intent": intent,
                "slots": slots,
                "missing": missing,
                "confidence": 0.9,
            }
    # 无匹配 → 回退（继承上轮意图便于多轮）
    prev_intent = (context or {}).get("intent") if context else None
    return {
        "intent": prev_intent or "fallback",
        "slots": (context or {}).get("slots", {}),
        "missing": [],
        "confidence": 0.3,
    }


# 追问话术模板
CLARIFY_TPL = {
    "city": "请问您想查询哪个城市的天气？",
    "order_id": "请告诉我订单号，例如 A1024。",
}
