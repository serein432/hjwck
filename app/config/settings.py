# -*- coding: utf-8 -*-
"""
配置中心：加载 config/config.yaml 与内置默认值，支持运行期热更新。
默认值即文档第 11/12 章推荐方案（Q1-Q8 已按默认执行并在 README 标注）。
所有配置项均可被 config.yaml 覆盖；运行期 PUT /api/v1/config 热生效。
"""
import os
import copy
import threading

# 项目根目录（app/config/settings.py 向上两级）
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.path.join(BASE_DIR, "data")
LOG_DIR = os.path.join(DATA_DIR, "logs")
AUDIO_DIR = os.path.join(DATA_DIR, "audio")
MODEL_DIR = os.path.join(BASE_DIR, "models")
CONFIG_PATH = os.path.join(BASE_DIR, "config", "config.yaml")

# 确保数据目录存在
for _d in (DATA_DIR, LOG_DIR, AUDIO_DIR, MODEL_DIR):
    os.makedirs(_d, exist_ok=True)

# 默认配置（混合模式：本地离线底座 + 在线可选，Q1-Q8 默认值）
DEFAULTS = {
    "system": {
        "name": "智能语音播报与语音交互系统",
        "version": "1.0.0",
        "mode": "hybrid",          # 混合模式（Q2）：在线优先，断网回退离线
        "language": "zh",          # 界面语言 zh/en
        "theme": "dark",           # 主题 dark/light
    },
    "server": {
        "host": "127.0.0.1",
        "port": 9090,
        "api_token": "vsb-dev-token",   # 开发默认 Token，生产须改
    },
    "db": {
        "path": os.path.join(DATA_DIR, "vsb.db"),
    },
    "queue": {
        "retry_max": 3,           # 默认重试次数（FR-019）
        "dedup_window": 60,       # 默认去重窗口秒（FR-018）
        "max_size": 1000,         # 队列容量上限（FR-022/NFR-004）
        "expire_sec": 300,        # 任务过期秒
        "interruption": True,     # 报警插队开关（FR-017）
        "mute_hours": {"start": 22, "end": 7},  # 静音时段：夜间不播普通/重要，仅报警（FR-021）
    },
    "voice": {
        "wake_word": "小帮手",     # 默认唤醒词（Q7，可配）
        "wake_mode": "energy",    # energy 能量唤醒 / porcupine 引擎（Q6）
        "wake_threshold": 0.6,    # 能量唤醒阈值（RMS）
        "asr": {
            "backend": "auto",    # auto: 有网用在线，否则离线
            "offline": "vosk",    # 离线 ASR 引擎
            "online": "none",     # 在线 ASR 占位（可接讯飞/Azure）
            "model_dir": MODEL_DIR,
        },
        "tts": {
            "backend": "auto",    # auto: 有网 edge-tts，否则 sapi
            "online": "edge-tts", # 在线 TTS
            "offline": "sapi",    # 离线 TTS（Windows SAPI）
            "voice": "zh-CN-XiaoxiaoNeural",  # edge-tts 默认音色
            "speed": 1.0,
            "volume": 100,        # 0-100 音量
        },
        "echo": {
            "strategy": "pause_asr",  # 播报期暂停识别（Q6 默认，AEC 可选）
        },
    },
    "mqtt": {
        "enabled": False,         # 默认关，需本地/远程 Broker 时开启
        "host": "127.0.0.1",
        "port": 1883,
        "username": "",
        "password": "",
        "client_id": "vsb-001",
    },
    "trigger": {
        "db_poll_interval": 5,    # 数据库轮询间隔秒（FR-026）
        "sensor_poll_interval": 2,  # 传感器轮询间隔秒（FR-028）
    },
    "auth": {
        "admin": {"username": "admin", "password": "admin123"},
        "user": {"username": "user", "password": "user123"},
    },
}


class Settings:
    """线程安全的配置容器，支持深合并与运行期更新（热生效）。"""

    def __init__(self):
        self._lock = threading.RLock()
        self._data = copy.deepcopy(DEFAULTS)
        self._load_file()

    def _load_file(self):
        """从 config.yaml 加载覆盖（若存在）。"""
        if not os.path.exists(CONFIG_PATH):
            return
        try:
            import yaml
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                user = yaml.safe_load(f) or {}
            with self._lock:
                self._merge(user, self._data)
        except Exception as e:  # 配置损坏不致命，回退默认
            print(f"[config] 加载 config.yaml 失败，使用默认: {e}")

    @staticmethod
    def _merge(src, dst):
        """递归深合并 src 到 dst（src 优先）。"""
        for k, v in src.items():
            if isinstance(v, dict) and isinstance(dst.get(k), dict):
                Settings._merge(v, dst[k])
            else:
                dst[k] = v

    def get(self, *keys, default=None):
        """按路径取值，如 get('voice','tts','voice')。"""
        with self._lock:
            node = self._data
            for k in keys:
                if isinstance(node, dict) and k in node:
                    node = node[k]
                else:
                    return default
            return node

    def set(self, *keys, value=None):
        """按路径设值（运行期热更新）。"""
        with self._lock:
            node = self._data
            for k in keys[:-1]:
                node = node.setdefault(k, {})
            node[keys[-1]] = value

    def raw(self):
        with self._lock:
            return copy.deepcopy(self._data)

    def reload(self):
        """重新加载配置文件（热重载）。"""
        with self._lock:
            self._data = copy.deepcopy(DEFAULTS)
            self._load_file()


# 全局单例
settings = Settings()
