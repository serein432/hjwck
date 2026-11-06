# -*- coding: utf-8 -*-
"""
SQLite 数据访问层（Q1：本地轻量用 SQLite）。
线程安全（每线程连接 + 锁），自动建表。表结构见文档第 9 章。
"""
import os
import json
import time
import sqlite3
import threading
from contextlib import contextmanager

from app.config.settings import settings, DATA_DIR

_DB_PATH = settings.get("db", "path", default=os.path.join(DATA_DIR, "vsb.db"))

# 建表语句（对齐文档 9.2 + notify_out）
SCHEMA = [
    """CREATE TABLE IF NOT EXISTS broadcast_task (
        task_id     INTEGER PRIMARY KEY AUTOINCREMENT,
        priority    INTEGER NOT NULL DEFAULT 0,
        content     TEXT NOT NULL,
        source      TEXT NOT NULL,
        type        TEXT,
        dedup_key   TEXT,
        dedup_window INTEGER DEFAULT 60,
        retry_max   INTEGER DEFAULT 3,
        status      TEXT NOT NULL DEFAULT 'pending',
        retry_count INTEGER DEFAULT 0,
        created_at  TEXT NOT NULL,
        expire_at   TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS idx_task_status_priority ON broadcast_task(status, priority)",
    """CREATE TABLE IF NOT EXISTS broadcast_log (
        log_id     INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id    INTEGER,
        content    TEXT,
        source     TEXT,
        priority   INTEGER,
        result     TEXT,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS skill_config (
        skill_id TEXT PRIMARY KEY,
        name     TEXT NOT NULL,
        intent   TEXT NOT NULL,
        handler  TEXT NOT NULL,
        enabled  INTEGER DEFAULT 1,
        config   TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS broadcast_template (
        tpl_id    TEXT PRIMARY KEY,
        type      TEXT NOT NULL,
        template  TEXT NOT NULL,
        variables TEXT,
        enabled   INTEGER DEFAULT 1
    )""",
    """CREATE TABLE IF NOT EXISTS trigger_source (
        source_id TEXT PRIMARY KEY,
        type      TEXT NOT NULL,
        name      TEXT,
        params    TEXT,
        enabled   INTEGER DEFAULT 1
    )""",
    """CREATE TABLE IF NOT EXISTS config_item (
        key      TEXT PRIMARY KEY,
        value    TEXT,
        grp      TEXT,
        version  INTEGER DEFAULT 1
    )""",
    """CREATE TABLE IF NOT EXISTS asr_log (
        log_id   INTEGER PRIMARY KEY AUTOINCREMENT,
        text     TEXT,
        intent   TEXT,
        conf     REAL,
        audio_ref TEXT,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS alert (
        alert_id INTEGER PRIMARY KEY AUTOINCREMENT,
        source   TEXT,
        level    INTEGER,
        message  TEXT,
        ack      INTEGER DEFAULT 0,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS notify_out (
        id        INTEGER PRIMARY KEY AUTOINCREMENT,
        msg_type  TEXT NOT NULL,
        content   TEXT NOT NULL,
        priority  INTEGER DEFAULT 0,
        dedup_key TEXT,
        created_at TEXT NOT NULL,
        processed INTEGER DEFAULT 0
    )""",
    """CREATE TABLE IF NOT EXISTS session_state (
        session_id TEXT PRIMARY KEY,
        data       TEXT,
        updated_at TEXT NOT NULL
    )""",
]


class Database:
    def __init__(self, path):
        self.path = path
        self._local = threading.local()
        self._init_lock = threading.Lock()
        self._init_lock.acquire()
        try:
            self._init()
        finally:
            self._init_lock.release()

    def _conn(self):
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(self.path, check_same_thread=False)
            self._local.conn.row_factory = sqlite3.Row
            self._local.conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn.execute("PRAGMA busy_timeout=3000")
        return self._local.conn

    def _init(self):
        c = self._conn()
        for stmt in SCHEMA:
            c.execute(stmt)
        c.commit()
        self._seed()

    def _seed(self):
        """首次初始化写入默认模板/技能/触发源（幂等）。"""
        c = self._conn()
        now = _now()
        # 默认播报模板（FR-008~014 七类）
        tpls = [
            ("fixed_opening", "fixed", "大家好，欢迎光临本店，营业时间九点到晚上十点。", None),
            ("weather", "weather", "{city}今天{date}{phenomenon}，气温{low}到{high}度，{wind}。", None),
            ("time", "time", "现在是{year}年{month}月{day}日，星期{weekday}，{hour}点{minute}分。", None),
            ("order", "order", "新订单已到达，订单号{order_id}，金额{amount}元，商品{item}。", None),
            ("alert", "alert", "注意：{message}，请立即处理。", None),
            ("sensor", "sensor", "{name}当前{unit}为{value}。", None),
            ("db", "db", "{content}", None),
        ]
        for t in tpls:
            c.execute("INSERT OR IGNORE INTO broadcast_template (tpl_id,type,template,variables,enabled) VALUES (?,?,?,?,1)", t)
        # 默认技能（FR-005 技能可路由）
        skills = [
            ("skill_time", "查询时间", "query_time", "builtin.time"),
            ("skill_weather", "查询天气", "query_weather", "builtin.weather"),
            ("skill_order", "查询订单", "query_order", "builtin.order"),
            ("skill_greet", "问候闲聊", "greet", "builtin.greet"),
        ]
        for s in skills:
            c.execute("INSERT OR IGNORE INTO skill_config (skill_id,name,intent,handler,enabled,config) VALUES (?,?,?,?,1,?)", s + (None,))
        # 默认触发源（示例，默认关，供界面展示与开启）
        trig = [
            ("trig_time_hourly", "cron", "整点报时", json.dumps({"cron": "0 * * * *", "type": "time"})),
            ("trig_weather_daily", "cron", "每日天气", json.dumps({"cron": "0 8 * * *", "type": "weather", "city": "北京"})),
            ("trig_mqtt_alert", "mqtt", "MQTT报警", json.dumps({"topic": "sys/alert/in"})),
            ("trig_sensor_sim", "sensor", "传感器(模拟)", json.dumps({"name": "sim", "unit": "℃", "threshold": 35})),
        ]
        for t in trig:
            c.execute("INSERT OR IGNORE INTO trigger_source (source_id,type,name,params,enabled) VALUES (?,?,?,?,0)", t)
        c.commit()

    @contextmanager
    def cursor(self):
        c = self._conn()
        try:
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise

    def query(self, sql, args=()):
        with self.cursor() as c:
            return [dict(r) for r in c.execute(sql, args).fetchall()]

    def query_one(self, sql, args=()):
        rows = self.query(sql, args)
        return rows[0] if rows else None

    def execute(self, sql, args=()):
        with self.cursor() as c:
            cur = c.execute(sql, args)
            return cur.lastrowid

    def executemany(self, sql, seq):
        with self.cursor() as c:
            c.executemany(sql, seq)


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


# 全局单例
db = Database(_DB_PATH)
