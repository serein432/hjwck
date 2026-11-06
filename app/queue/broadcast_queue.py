# -*- coding: utf-8 -*-
"""
播报队列与调度（FR-016~022 / 8.7 / NFR-004,005,006）：
- 三级优先级：0 普通 / 1 重要 / 2 报警
- 报警插队（FR-017）、去重窗口（FR-018）、重试（FR-019）、暂停/恢复（FR-020）
- 静音时段（FR-021：夜间仅播报警）、容量上限与超时淘汰（FR-022）
- 单音频输出互斥（同一时刻仅一路播放，NFR-006）
调度线程独立，TTS 合成在 worker 线程执行，避免阻塞事件循环。
"""
import time
import uuid
import heapq
import threading

from app.config.settings import settings
from app.db.database import db
from app.logging import app_logger as lg
from app.nlp.normalize import normalize

# 优先级常量
P_NORMAL, P_IMPORTANT, P_ALERT = 0, 1, 2


class BroadcastQueue:
    def __init__(self):
        self._heap = []          # 优先级堆：(-priority, seq, task)
        self._seq = 0
        self._lock = threading.Condition()
        self._paused = False
        self._closed = False
        self._playing = False    # 音频互斥：是否正在播放
        self._stop_event = threading.Event()
        self._worker = threading.Thread(target=self._run, name="bcast-queue", daemon=True)
        self._worker.start()

    # ---------- 入队 ----------
    def enqueue(self, content, source="manual", priority=P_NORMAL, type_=None,
                dedup_key=None, dedup_window=None, retry_max=None,
                expire_sec=None, idempotency_key=None):
        """
        入队一条播报任务。返回 (task_id, deduped)：deduped=True 表示被去重/幂等拦截。
        幂等：同一 idempotency_key 在窗口内不重复入队（NFR-007 写接口幂等）。
        """
        dedup_window = dedup_window if dedup_window is not None else settings.get("queue", "dedup_window", default=60)
        retry_max = retry_max if retry_max is not None else settings.get("queue", "retry_max", default=3)
        expire_sec = expire_sec if expire_sec is not None else settings.get("queue", "expire_sec", default=300)

        # 幂等键（显式传入）与去重键：合并为一次原子「检查+写入」
        dedup_keys = []
        if idempotency_key:
            dedup_keys.append("idem:" + idempotency_key)
        if dedup_key:
            dedup_keys.append("dedup:" + dedup_key)
        if dedup_keys and self.check_and_mark(dedup_keys, dedup_window):
            lg.log("info", "queue", "去重/幂等拦截", key=dedup_key or idempotency_key)
            return None, True

        now = time.time()
        task_id = "T-" + time.strftime("%Y%m%d") + "-" + uuid.uuid4().hex[:6]
        task = {
            "task_id": task_id,
            "priority": priority,
            "content": content,
            "source": source,
            "type": type_,
            "dedup_key": dedup_key,
            "dedup_window": dedup_window,
            "retry_max": retry_max,
            "retry_count": 0,
            "created_at": now,
            "expire_at": now + expire_sec,
            "status": "pending",
            "_norm": normalize(content),   # 规范化文本
        }
        with self._lock:
            # 容量上限（FR-022）
            max_size = settings.get("queue", "max_size", default=1000)
            if len(self._heap) >= max_size:
                # 淘汰最低优先级且最旧的
                self._heap.sort(key=lambda x: (x[0], x[1]))
                evicted = self._heap.pop(0)
                self._persist(evicted[2], status="dropped", reason="queue_full")
                lg.log("warn", "queue", "队列满，淘汰低优任务", task=evicted[2]["task_id"])
            self._seq += 1
            heapq.heappush(self._heap, (-priority, self._seq, task))
            self._persist(task, status="pending")
            self._lock.notify()  # 持锁时唤醒调度线程
        lg.log("info", "queue", "任务入队", task=task_id, priority=priority, source=source)
        return task_id, False

    # ---------- 去重状态（进程内 TTL，文档 9.4 用 Redis，此处本地等价实现） ----------
    _recent_map = {}
    _recent_lock = threading.Lock()

    @classmethod
    def check_and_mark(cls, keys, window):
        """
        原子化「检查 + 写入」：keys 中任一已存在且未过期 → 返回 True（拦截）；
        否则把全部 keys 写入 TTL 并返回 False（放行）。
        合并进同一把锁，杜绝「检查后写入」的竞态窗口。
        """
        now = time.time()
        mark_until = now + window
        with cls._recent_lock:
            # 先清理过期，再判断
            for k in [k for k, v in cls._recent_map.items() if now > v]:
                del cls._recent_map[k]
            for k in keys:
                exp = cls._recent_map.get(k)
                if exp and now < exp:
                    return True  # 命中，拦截
            for k in keys:
                cls._recent_map[k] = mark_until
            return False  # 未命中，写入并放行

    # ---------- 控制 ----------
    def pause(self):
        with self._lock:
            self._paused = True
        lg.log("info", "queue", "队列已暂停")

    def resume(self):
        with self._lock:
            self._paused = False
            self._lock.notify()
        lg.log("info", "queue", "队列已恢复")

    def is_paused(self):
        with self._lock:
            return self._paused

    def pending_count(self):
        with self._lock:
            return len(self._heap)

    def snapshot(self, limit=50):
        """队列快照（供界面实时监控）：按优先级排序的待播任务。"""
        with self._lock:
            items = sorted(self._heap, key=lambda x: (-x[0], x[1]))
            return [
                {
                    "task_id": t["task_id"], "priority": -neg, "source": t["source"],
                    "type": t["type"], "content": t["content"],
                    "normalized": t["_norm"], "created_at": t["created_at"],
                    "retry_count": t["retry_count"],
                }
                for neg, _, t in items[:limit]
            ]

    def state(self):
        with self._lock:
            return {"paused": self._paused, "playing": self._playing, "pending": len(self._heap)}

    # ---------- 静音时段（FR-021） ----------
    def _in_mute(self, priority):
        """静音时段内，仅报警级可播；普通/重要延迟（重新入队）。"""
        if priority == P_ALERT:
            return False
        mh = settings.get("queue", "mute_hours", default={})
        if not mh:
            return False
        h = time.localtime().tm_hour
        start, end = mh.get("start", 22), mh.get("end", 7)
        if start <= end:
            return start <= h < end
        return h >= start or h < end

    # ---------- 调度主循环 ----------
    def _run(self):
        while not self._stop_event.is_set():
            task = None
            with self._lock:
                while not self._heap and not self._stop_event.is_set():
                    self._lock.wait(timeout=0.5)
                # 暂停期间：报警仍插队，其余等待
                while self._heap:
                    neg, _, t = self._heap[0]
                    pri = -neg
                    if self._paused and pri < P_ALERT:
                        break
                    # 过期淘汰（FR-022）
                    if time.time() > t.get("expire_at", 0):
                        heapq.heappop(self._heap)
                        self._persist(t, status="expired", reason="timeout")
                        lg.log("warn", "queue", "任务过期淘汰", task=t["task_id"])
                        continue
                    # 静音时段：普通/重要延迟
                    if self._in_mute(pri):
                        # 重新入队，稍后再试
                        heapq.heappop(self._heap)
                        self._seq += 1
                        t["created_at"] = time.time()
                        t["expire_at"] = time.time() + 30  # 静音延迟最多 30s 后重试
                        heapq.heappush(self._heap, (neg, self._seq, t))
                        break
                    task = heapq.heappop(self._heap)
                    break
            if task is None:
                continue
            self._dispatch(task[2])

    def _dispatch(self, task):
        """执行单条任务：TTS 合成 + 播放（在 worker 线程，避免阻塞事件循环）。"""
        with self._lock:
            self._playing = True
        try:
            lg.log("info", "queue", "开始播报", task=task["task_id"], priority=task["priority"])
            ok = self._play_with_retry(task)
            status = "done" if ok else "failed"
            self._persist(task, status=status, result="ok" if ok else "play_failed")
            self._ack(task, ok)
        finally:
            with self._lock:
                self._playing = False

    def _play_with_retry(self, task):
        """带重试的播放（FR-019）。"""
        max_retry = task.get("retry_max", 0)
        while True:
            try:
                ok = self._play_once(task)
                if ok:
                    return True
            except Exception as e:
                lg.log("error", "queue", "播放异常", task=task["task_id"], err=str(e))
            task["retry_count"] += 1
            if task["retry_count"] > max_retry:
                return False
            time.sleep(1.0)

    def _play_once(self, task):
        """单次播放：调用 TTS 适配器合成并播放。延迟导入避免循环依赖。"""
        from app.tts.tts_service import tts_service
        return tts_service.speak(task["_norm"], priority=task["priority"])

    # ---------- 持久化与回执 ----------
    def _persist(self, task, status=None, result=None, reason=None):
        try:
            now = time.strftime("%Y-%m-%d %H:%M:%S")
            if status:
                task["status"] = status
            db.execute(
                "INSERT INTO broadcast_log (task_id,content,source,priority,result,created_at) VALUES (?,?,?,?,?,?)",
                (task["task_id"], task["content"], task["source"], task["priority"],
                 (result or status) + (f":{reason}" if reason else ""), now)
            )
        except Exception as e:
            lg.log("error", "queue", "持久化失败", err=str(e))

    def _ack(self, task, ok):
        """状态回执 + 指标 + 审计日志（FR-037/039）。"""
        lg.inc("broadcast_count")
        if ok:
            lg.inc("broadcast_ok")
        else:
            lg.inc("broadcast_fail")
        lg.audit_log(task["task_id"], task["content"], task["source"], task["priority"],
                     "success" if ok else "failed")
        if task["priority"] == P_ALERT:
            # 报警时效（从创建到播完）
            lg.metric_alert_time(int((time.time() - task["created_at"]) * 1000))
        # 推送给 App（延迟导入避免循环依赖）
        try:
            from app.core.hub import hub
            hub.push("queue_event", {
                "task_id": task["task_id"], "status": "done" if ok else "failed",
                "content": task["content"], "priority": task["priority"],
                "source": task["source"], "playing": ok,
            })
        except Exception:
            pass

    def stop(self):
        self._stop_event.set()
        with self._lock:
            self._lock.notify_all()


# 全局单例
queue = BroadcastQueue()
