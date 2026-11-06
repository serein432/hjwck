# -*- coding: utf-8 -*-
"""
触发与集成模块（FR-023~030 / 8.8 / M4）：
六类触发源统一归一化为标准播报任务（FR-030），适配器化。
  1. 定时 cron（APScheduler，FR-023）
  2. HTTP（已在 routes.py，FR-024）
  3. MQTT（paho-mqtt，FR-025）
  4. 数据库轮询 notify_out 表（FR-026）
  5. 传感器（模拟/阈值，FR-028）
  6. 手动按钮（API/界面，FR-029）
每个触发源独立可启停，配置来自 trigger_source 表。
"""
import time
import json
import threading

from app.config.settings import settings
from app.db.database import db
from app.logging import app_logger as lg
from app.queue.broadcast_queue import queue, P_NORMAL, P_IMPORTANT, P_ALERT

# 模板类型 → 优先级映射
_TYPE_PRIORITY = {
    "alert": P_ALERT, "order": P_IMPORTANT, "sensor": P_NORMAL,
    "weather": P_NORMAL, "time": P_NORMAL, "fixed": P_NORMAL, "db": P_NORMAL,
}


def _render(template, variables):
    """模板变量替换（FR-008 变量占位）。"""
    try:
        if variables:
            return template.format(**variables)
        return template
    except Exception:
        return template


def _get_template(tpl_type, tpl_id=None):
    """取模板（优先 tpl_id，否则按类型取第一个启用模板）。"""
    if tpl_id:
        row = db.query_one("SELECT template FROM broadcast_template WHERE tpl_id=? AND enabled=1", (tpl_id,))
    else:
        row = db.query_one("SELECT template FROM broadcast_template WHERE type=? AND enabled=1 LIMIT 1", (tpl_type,))
    return row["template"] if row else None


# ==================== 1. 定时任务 ====================
_scheduler = None
_scheduler_started = False


def _cron_job_time():
    """整点/半点报时（示例定时播报）。"""
    queue.enqueue(_render(_get_template("time") or "现在是{hour}点。",
                          {"year": time.localtime().tm_year, "month": time.localtime().tm_mon,
                           "day": time.localtime().tm_mday, "weekday": "一二三四五六日"[time.localtime().tm_wday],
                           "hour": time.localtime().tm_hour, "minute": time.localtime().tm_min}),
                source="cron", priority=P_NORMAL, type_="time", dedup_key="time:" + time.strftime("%H"))


def _cron_job_weather(city):
    tpl = _get_template("weather") or "{city}{phenomenon}，{low}到{high}度。"
    # 演示天气（可替换真实 API）
    import random
    w = random.choice([("晴", 18, 28, "东北风2级"), ("多云", 16, 25, "东南风3级")])
    queue.enqueue(_render(tpl, {"city": city, "date": "今天", "phenomenon": w[0],
                                "low": w[1], "high": w[2], "wind": w[3]}),
                  source="cron", priority=P_NORMAL, type_="weather", dedup_key=f"weather:{city}")


def start_scheduler():
    """启动 APScheduler，加载 trigger_source 中 type=cron 且启用的任务。"""
    global _scheduler, _scheduler_started
    if _scheduler_started:
        return
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
    except Exception as e:
        lg.log("error", "trigger", "APScheduler 不可用", err=str(e))
        return
    _scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
    for row in db.query("SELECT * FROM trigger_source WHERE type='cron' AND enabled=1"):
        try:
            p = json.loads(row["params"]) if row["params"] else {}
            cron = p.get("cron", "0 * * * *")
            parts = cron.split()
            if len(parts) != 5:
                continue
            job_id = row["source_id"]
            if p.get("type") == "time":
                _scheduler.add_job(_cron_job_time, "cron", id=job_id,
                                   minute=parts[0], hour=parts[1], day=parts[2],
                                   month=parts[3], day_of_week=parts[4])
            elif p.get("type") == "weather":
                city = p.get("city", "北京")
                _scheduler.add_job(_cron_job_weather, "cron", args=[city], id=job_id,
                                   minute=parts[0], hour=parts[1], day=parts[2],
                                   month=parts[3], day_of_week=parts[4])
            lg.log("info", "trigger", "定时任务已加载", id=job_id, cron=cron)
        except Exception as e:
            lg.log("error", "trigger", "定时任务加载失败", id=row["source_id"], err=str(e))
    _scheduler.start()
    _scheduler_started = True
    lg.log("info", "trigger", "定时调度器已启动")


def reload_scheduler():
    """配置变更后重载定时任务（热更新）。"""
    global _scheduler
    if _scheduler:
        try:
            for j in list(_scheduler.get_jobs()):
                j.remove()
        except Exception:
            pass
    start_scheduler()


# ==================== 3. MQTT ====================
_mqtt_client = None
_mqtt_started = False


def _on_mqtt_message(client, userdata, msg):
    """MQTT 消息 → 播报任务（FR-025 / 10.2 Topic）。"""
    topic = msg.topic
    try:
        payload = json.loads(msg.payload.decode("utf-8"))
    except Exception:
        payload = {"content": msg.payload.decode("utf-8", "ignore")}
    # 按 topic 映射类型
    if topic.endswith("sys/alert/in"):
        t, pri = "alert", P_ALERT
    elif topic.endswith("sys/sensor/data"):
        t, pri = "sensor", P_NORMAL
    elif topic.endswith("sys/broadcast/req"):
        t, pri = payload.get("type", "db"), int(payload.get("priority", 0))
    elif topic.endswith("sys/voice/command"):
        t, pri = "fixed", P_IMPORTANT
    else:
        t, pri = "db", P_NORMAL
    content = payload.get("content", "")
    if not content:
        return
    dedup = payload.get("dedup_key") or f"mqtt:{topic}:{content[:20]}"
    task_id, deduped = queue.enqueue(content, source=f"mqtt:{topic}", priority=pri,
                                     type_=t, dedup_key=dedup)
    if not deduped:
        # 发布回执（FR-025 / 10.2 sys/broadcast/ack）
        try:
            client.publish("sys/broadcast/ack",
                           json.dumps({"task_id": task_id, "topic": topic}))
        except Exception:
            pass
    lg.log("info", "trigger", "MQTT 触发", topic=topic, task=task_id, deduped=deduped)


def start_mqtt():
    """启动 MQTT 客户端（配置启用时）。"""
    global _mqtt_client, _mqtt_started
    if _mqtt_started or not settings.get("mqtt", "enabled"):
        return
    try:
        import paho.mqtt.client as mqtt
    except Exception as e:
        lg.log("error", "trigger", "paho-mqtt 不可用", err=str(e))
        return
    try:
        host = settings.get("mqtt", "host", default="127.0.0.1")
        port = int(settings.get("mqtt", "port", default=1883))
        user = settings.get("mqtt", "username", default="")
        pwd = settings.get("mqtt", "password", default="")
        cid = settings.get("mqtt", "client_id", default="vsb-001")
        client = mqtt.Client(client_id=cid)
        if user:
            client.username_pw_set(user, pwd)
        client.on_message = _on_mqtt_message
        client.on_connect = lambda c, u, f, p: (
            c.subscribe(["sys/broadcast/req", "sys/alert/in", "sys/sensor/data", "sys/voice/command"]),
            lg.log("info", "trigger", "MQTT 已连接并订阅", host=host))
        client.connect(host, port, 60)
        client.loop_start()
        _mqtt_client = client
        _mqtt_started = True
        lg.log("info", "trigger", "MQTT 客户端已启动", host=host)
    except Exception as e:
        lg.log("error", "trigger", "MQTT 启动失败", err=str(e))


# ==================== 4. 数据库轮询 ====================
_db_poll_started = False


def _db_poll_once():
    """扫描 notify_out 表 processed=0 的记录 → 播报（FR-026 / 10.3）。"""
    rows = db.query("SELECT * FROM notify_out WHERE processed=0 ORDER BY id ASC LIMIT 50")
    for r in rows:
        content = r["content"]
        if not content:
            db.execute("UPDATE notify_out SET processed=1 WHERE id=?", (r["id"],))
            continue
        t = r.get("msg_type", "db")
        pri = min(int(r.get("priority", 0)), P_ALERT)
        dedup = r.get("dedup_key") or f"db:{r['id']}"
        task_id, deduped = queue.enqueue(content, source=f"db:{t}", priority=pri,
                                         type_=t, dedup_key=dedup)
        db.execute("UPDATE notify_out SET processed=1 WHERE id=?", (r["id"],))
        if not deduped:
            lg.log("info", "trigger", "DB轮询触发", id=r["id"], task=task_id)


def start_db_poll():
    """启动数据库轮询线程（FR-026）。"""
    global _db_poll_started
    if _db_poll_started:
        return
    _db_poll_started = True

    def _loop():
        while True:
            try:
                _db_poll_once()
            except Exception as e:
                lg.log("error", "trigger", "DB轮询异常", err=str(e))
            time.sleep(int(settings.get("trigger", "db_poll_interval", default=5)))
    threading.Thread(target=_loop, daemon=True, name="db-poll").start()
    lg.log("info", "trigger", "数据库轮询已启动")


def inject_db_message(msg_type, content, priority=0, dedup_key=None):
    """向 notify_out 注入一条消息（演示/测试 DB 触发，模拟外部业务系统写入）。"""
    db.execute("INSERT INTO notify_out (msg_type,content,priority,dedup_key,created_at,processed) VALUES (?,?,?,?,'%s',0)" % time.strftime("%Y-%m-%d %H:%M:%S"),
               (msg_type, content, priority, dedup_key))
    return True


# ==================== 5. 传感器（模拟/阈值） ====================
_sensor_started = False
_sensor_state = {}


def _sensor_once():
    """读取传感器（模拟源）→ 阈值/定时播报（FR-028）。"""
    for row in db.query("SELECT * FROM trigger_source WHERE type='sensor' AND enabled=1"):
        try:
            p = json.loads(row["params"]) if row["params"] else {}
            name = p.get("name", row["source_id"])
            unit = p.get("unit", "℃")
            threshold = float(p.get("threshold", 35))
            # 模拟传感器值（正弦波动 30~40）
            import math
            val = 35 + 5 * math.sin(time.time() / 10)
            _sensor_state[name] = val
            tpl = _get_template("sensor") or "{name}当前{unit}为{value}。"
            if val >= threshold:
                # 阈值触发（报警级）
                dedup = f"sensor:{name}:over"
                task_id, deduped = queue.enqueue(
                    _render(tpl, {"name": name, "unit": unit, "value": round(val, 1)}) +
                    f"已超过阈值{threshold}{unit}，请注意！",
                    source=f"sensor:{name}", priority=P_ALERT, type_="sensor",
                    dedup_key=dedup, dedup_window=30)
                if not deduped:
                    lg.log("warn", "trigger", "传感器阈值触发", name=name, val=round(val, 1))
        except Exception as e:
            lg.log("error", "trigger", "传感器触发异常", err=str(e))


def start_sensor():
    """启动传感器轮询线程（FR-028）。"""
    global _sensor_started
    if _sensor_started:
        return
    _sensor_started = True

    def _loop():
        while True:
            try:
                _sensor_once()
            except Exception as e:
                lg.log("error", "trigger", "传感器轮询异常", err=str(e))
            time.sleep(int(settings.get("trigger", "sensor_poll_interval", default=2)))
    threading.Thread(target=_loop, daemon=True, name="sensor-poll").start()
    lg.log("info", "trigger", "传感器轮询已启动")


def sensor_state():
    return dict(_sensor_state)


# ==================== 6. 手动按钮 ====================
def button_trigger(tpl_id=None, content=None, name="手动"):
    """手动按钮触发（FR-029 / 界面按钮）。"""
    if content:
        c = content
    else:
        tpl = _get_template("fixed", tpl_id) or "手动触发播报。"
        c = _render(tpl, {})
    task_id, deduped = queue.enqueue(c, source=f"button:{name}", priority=P_IMPORTANT,
                                     type_="fixed", dedup_key=f"btn:{name}:{int(time.time())}")
    lg.log("info", "trigger", "按钮触发", name=name, task=task_id)
    return task_id, deduped


# ==================== 统一启动 ====================
def start_all_triggers():
    """启动所有触发源（应用启动时调用）。"""
    start_scheduler()
    start_mqtt()
    start_db_poll()
    start_sensor()
    lg.log("info", "trigger", "全部触发源已启动")


def reload_triggers():
    """配置变更热重载（界面切换开关后调用）。"""
    reload_scheduler()
    lg.log("info", "trigger", "触发源已重载")
