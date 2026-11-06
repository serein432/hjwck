# -*- coding: utf-8 -*-
"""
FastAPI 路由（文档第 10 章接口 + 8 大功能界面所需数据接口）。
统一响应 {code,msg,data}；写接口幂等+限流；Bearer 鉴权（管理操作需 admin）。
"""
import os
import time
import json
import asyncio
import threading
from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, FileResponse, HTMLResponse

from app.config.settings import settings, AUDIO_DIR, BASE_DIR
from app.db.database import db
from app.logging import app_logger as lg
from app.api.response import (ok, err, wrap, require_auth, require_rate_limit,
                              login, get_role)
from app.queue.broadcast_queue import queue, P_NORMAL, P_IMPORTANT, P_ALERT
from app.core.voice_engine import engine
from app.core.hub import hub
from app.wakeword.detector import detector
from app.asr.asr_service import asr_service
from app.tts.tts_service import tts_service
from app.nlp.normalize import normalize
from app.skills import router as skill_router

router = APIRouter()

# 桌面界面 HTML 路径（app/desktop/index.html）
# routes.py 在 app/api/ 下：3 次 dirname → 项目根，再拼 app/desktop/index.html
_HTML_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "app", "desktop", "index.html",
)


# ==================== 桌面界面（HTTP 提供，绕开中文路径 file:// 编码问题）====================
@router.get("/app/", include_in_schema=False)
@router.get("/app", include_in_schema=False)
def app_ui():
    """返回桌面 App 界面 HTML（全内联 JS/CSS，无外部资源）。
    用 HTTP 而非 file:// 加载，避免中文路径（python文件夹）未被 URL 编码导致 WebView 空白。"""
    if os.path.isfile(_HTML_PATH):
        with open(_HTML_PATH, "r", encoding="utf-8") as f:
            return HTMLResponse(f.read())
    return HTMLResponse("<h2>index.html not found</h2>", status_code=404)


# ==================== 健康检查 ====================
@router.get("/health")
def health():
    """健康检查（FR-039 / 14 监控 / 附录B）。"""
    return ok({
        "status": "ok",
        "version": settings.get("system", "version"),
        "mode": settings.get("system", "mode"),
        "queue": queue.state(),
        "asr_ready": asr_service.model_ready(),
        "clients": hub.client_count(),
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
    })


# ==================== 播报 ====================
@router.post("/api/v1/broadcast")
async def broadcast_post(request: Request):
    """推送播报任务（Bearer 鉴权 + 幂等 + 限流）。"""
    res = require_rate_limit(request)
    if res:
        return res
    res = require_auth(request)
    if res:
        return res
    body = await _body(request)
    return _enqueue_from_body(body, source="http")


@router.post("/api/v1/broadcast/now")
async def broadcast_now(request: Request):
    """立即播报（高优先级插队，FR-017）。"""
    res = require_rate_limit(request)
    if res:
        return res
    res = require_auth(request)
    if res:
        return res
    body = await _body(request)
    body["priority"] = P_ALERT  # 强制报警级插队
    return _enqueue_from_body(body, source="http_now")


@router.get("/api/v1/broadcast/status")
def broadcast_status(request: Request):
    """查询队列状态与最近播报。"""
    res = require_auth(request)
    if res:
        return res
    recent = db.query("SELECT * FROM broadcast_log ORDER BY log_id DESC LIMIT 20")
    return ok({
        "queue": queue.state(),
        "pending": queue.snapshot(50),
        "recent_logs": recent,
    })


async def _body(request: Request):
    """异步读取并解析 JSON body（异步端点内 await 才可靠）。"""
    try:
        return await request.json()
    except Exception:
        return {}


def _enqueue_from_body(body, source):
    """统一入队处理（幂等键 = idempotency_key 或 dedup_key）。"""
    content = (body.get("content") or "").strip()
    if not content:
        return err(400, "content 不能为空")
    if len(content) > 500:
        return err(400, "content 过长（>500字）")
    priority = int(body.get("priority", P_NORMAL))
    priority = max(P_NORMAL, min(P_ALERT, priority))
    task_id, deduped = queue.enqueue(
        content=content, source=source, priority=priority,
        type_=body.get("type"), dedup_key=body.get("dedup_key"),
        dedup_window=body.get("dedup_window"), retry_max=body.get("retry_max"),
        idempotency_key=body.get("idempotency_key"),
    )
    if deduped:
        return ok({"task_id": None, "deduped": True}, msg="已去重/幂等拦截")
    return ok({"task_id": task_id})


# ==================== 队列控制（管理操作，需 admin） ====================
@router.post("/api/v1/queue/pause")
def queue_pause(request: Request):
    res = require_auth(request, need_admin=True)
    if res:
        return res
    queue.pause()
    return ok({"paused": True})


@router.post("/api/v1/queue/resume")
def queue_resume(request: Request):
    res = require_auth(request, need_admin=True)
    if res:
        return res
    queue.resume()
    return ok({"paused": False})


# ==================== 技能 ====================
@router.get("/api/v1/skills")
def skills_list(request: Request):
    res = require_auth(request)
    if res:
        return res
    return ok(skill_router.list_skills())


@router.post("/api/v1/skills/{skill_id}/toggle")
def skills_toggle(request: Request, skill_id: str):
    res = require_auth(request, need_admin=True)
    if res:
        return res
    enabled = not bool(db.query_one("SELECT enabled FROM skill_config WHERE skill_id=?", (skill_id,)) or {}).get("enabled", 1) if db.query_one("SELECT enabled FROM skill_config WHERE skill_id=?", (skill_id,)) else False
    skill_router.set_skill_enabled(skill_id, enabled)
    return ok({"skill_id": skill_id, "enabled": enabled})


# ==================== 配置（热更新，管理操作需 admin） ====================
@router.get("/api/v1/config")
def config_get(request: Request):
    res = require_auth(request)
    if res:
        return res
    return ok(settings.raw())


@router.put("/api/v1/config")
async def config_put(request: Request):
    res = require_rate_limit(request)
    if res:
        return res
    res = require_auth(request, need_admin=True)
    if res:
        return res
    body = await _body(request)
    # 支持按路径更新：body = {"path": ["voice","tts","voice"], "value": "..."}
    # 或 body = {"set": {"voice.tts.voice": "..."}}
    updated = []
    if "set" in body and isinstance(body["set"], dict):
        for dotted, val in body["set"].items():
            keys = dotted.split(".")
            settings.set(*keys, value=val)
            updated.append(dotted)
    elif "path" in body:
        keys = body["path"]
        settings.set(*keys, value=body.get("value"))
        updated.append(".".join(keys))
    lg.log("info", "config", "配置热更新", keys=updated)
    return ok({"updated": updated})


# ==================== TTS ====================
@router.post("/api/v1/tts")
async def tts_speak(request: Request):
    """文本转语音并播放（管理/演示）。"""
    res = require_rate_limit(request)
    if res:
        return res
    res = require_auth(request)
    if res:
        return res
    body = await _body(request)
    text = (body.get("text") or "").strip()
    if not text:
        return err(400, "text 不能为空")
    norm = normalize(text)
    ok_play = tts_service.speak(norm, priority=int(body.get("priority", 1)))
    return ok({"played": ok_play, "normalized": norm})


# ==================== 语音交互 ====================
@router.post("/api/v1/voice/text")
async def voice_text(request: Request):
    """文本注入语音链路（界面/测试用，无硬件可演示闭环）。"""
    res = require_rate_limit(request)
    if res:
        return res
    res = require_auth(request)
    if res:
        return res
    body = await _body(request)
    text = (body.get("text") or "").strip()
    # handle_text 内部含 TTS 播放（阻塞），放线程池避免阻塞事件循环
    result = await asyncio.get_event_loop().run_in_executor(None, engine.handle_text, text)
    return ok({"result": result, "status": engine.status()})


@router.post("/api/v1/voice/listen")
def voice_listen(request: Request):
    """开始监听（唤醒词）。"""
    res = require_auth(request)
    if res:
        return res
    engine.start_listening()
    return ok(engine.status())


@router.post("/api/v1/voice/stop")
def voice_stop(request: Request):
    res = require_auth(request)
    if res:
        return res
    engine.stop_listening()
    return ok(engine.status())


@router.post("/api/v1/voice/recognize")
def voice_recognize(request: Request):
    """处理一次录音（唤醒后）。"""
    res = require_auth(request)
    if res:
        return res
    # 异步处理录音（耗时），在后台线程
    def _work():
        try:
            engine.process_recording()
        except Exception as e:
            lg.log("error", "voice", "录音处理异常", err=str(e))
    threading.Thread(target=_work, daemon=True).start()
    return ok({"started": True, "status": engine.status()})


@router.get("/api/v1/voice/status")
def voice_status(request: Request):
    res = require_auth(request)
    if res:
        return res
    return ok(engine.status())


@router.get("/api/v1/voice/history")
def voice_history(request: Request):
    """对话记录（App 语音交互页）。"""
    res = require_auth(request)
    if res:
        return res
    # 从 hub 最近事件提取 result/asr
    recent = hub.recent(200)
    conv = []
    for e in recent:
        if e["event"] in ("result", "asr"):
            conv.append({"event": e["event"], "data": e["data"], "time": time.time()})
    return ok({"conversation": conv})


# ==================== 内容配置（模板） ====================
@router.get("/api/v1/templates")
def templates_list(request: Request):
    res = require_auth(request)
    if res:
        return res
    return ok(db.query("SELECT * FROM broadcast_template ORDER BY type"))


@router.put("/api/v1/templates/{tpl_id}")
async def templates_update(request: Request, tpl_id: str):
    res = require_rate_limit(request)
    if res:
        return res
    res = require_auth(request, need_admin=True)
    if res:
        return res
    body = await _body(request)
    template = body.get("template")
    variables = body.get("variables")
    if template is None and variables is None:
        return err(400, "需提供 template 或 variables")
    db.execute("UPDATE broadcast_template SET template=COALESCE(?,template), variables=COALESCE(?,variables) WHERE tpl_id=?",
               (template, json.dumps(variables) if variables else None, tpl_id))
    lg.log("info", "config", "模板热更新", tpl=tpl_id)
    return ok({"tpl_id": tpl_id, "updated": True})


# ==================== 触发源管理 ====================
@router.get("/api/v1/triggers")
def triggers_list(request: Request):
    res = require_auth(request)
    if res:
        return res
    rows = db.query("SELECT * FROM trigger_source ORDER BY source_id")
    for r in rows:
        try:
            r["params"] = json.loads(r["params"]) if r["params"] else {}
        except Exception:
            pass
    return ok(rows)


@router.post("/api/v1/triggers")
async def triggers_add(request: Request):
    res = require_rate_limit(request)
    if res:
        return res
    res = require_auth(request, need_admin=True)
    if res:
        return res
    body = await _body(request)
    sid = body.get("source_id") or "trig_" + str(int(time.time() * 1000))
    db.execute("INSERT OR REPLACE INTO trigger_source (source_id,type,name,params,enabled) VALUES (?,?,?,?,?)",
               (sid, body.get("type", "manual"), body.get("name", ""),
                json.dumps(body.get("params", {})), 1 if body.get("enabled", True) else 0))
    return ok({"source_id": sid})


@router.delete("/api/v1/triggers/{source_id}")
def triggers_del(request: Request, source_id: str):
    res = require_auth(request, need_admin=True)
    if res:
        return res
    db.execute("DELETE FROM trigger_source WHERE source_id=?", (source_id,))
    return ok({"deleted": source_id})


@router.post("/api/v1/triggers/{source_id}/toggle")
def triggers_toggle(request: Request, source_id: str):
    res = require_auth(request, need_admin=True)
    if res:
        return res
    row = db.query_one("SELECT enabled FROM trigger_source WHERE source_id=?", (source_id,))
    if not row:
        return err(404, "触发源不存在")
    new = 0 if row["enabled"] else 1
    db.execute("UPDATE trigger_source SET enabled=? WHERE source_id=?", (new, source_id))
    return ok({"source_id": source_id, "enabled": bool(new)})


# ==================== 监控与日志 ====================
@router.get("/api/v1/metrics")
def metrics(request: Request):
    res = require_auth(request)
    if res:
        return res
    snap = lg.snapshot()
    snap["queue"] = queue.state()
    snap["asr_ready"] = asr_service.model_ready()
    snap["clients"] = hub.client_count()
    return ok(snap)


@router.get("/api/v1/logs")
def logs(request: Request):
    """运行日志（最近 N 条，从 run.log 读取）。"""
    res = require_auth(request, need_admin=True)
    if res:
        return res
    n = int(request.query_params.get("limit", "100"))
    return ok({"lines": _tail_log(n)})


def _tail_log(n):
    from app.config.settings import LOG_DIR
    import os
    p = os.path.join(LOG_DIR, "vsb.log")
    if not os.path.exists(p):
        return []
    try:
        with open(p, "r", encoding="utf-8") as f:
            lines = f.readlines()
        return [l.rstrip() for l in lines[-n:]]
    except Exception:
        return []


# ==================== 认证与用户 ====================
@router.post("/api/v1/auth/login")
async def auth_login(request: Request):
    """用户登录（界面分级权限）。"""
    body = await _body(request)
    tok, role = login(body.get("username", ""), body.get("password", ""))
    if not tok:
        return err(401, "用户名或密码错误")
    return ok({"token": tok, "role": role, "username": body.get("username")})


@router.get("/api/v1/auth/me")
def auth_me(request: Request):
    res = require_auth(request)
    if res:
        return res
    return ok({"role": get_role(request)})


# ==================== 音频设备 ====================
@router.get("/api/v1/devices")
def devices(request: Request):
    res = require_auth(request)
    if res:
        return res
    from app.audio.capture import capture
    return ok({"inputs": capture.list_devices()})


# ==================== WebSocket 实时推送 ====================
@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    """实时事件推送（唤醒/波形/识别/意图/技能/队列/日志）。"""
    await ws.accept()
    hub.add_client(ws)
    hub.bind_loop(asyncio.get_running_loop())
    # 重连补拉最近事件
    try:
        await ws.send_text(json.dumps({"event": "hello",
                                       "data": {"recent": hub.recent(50)}},
                                      ensure_ascii=False))
    except Exception:
        hub.remove_client(ws)
        return
    try:
        while True:
            # 接收客户端指令（如 手动播报/停止/音量）
            msg = await ws.receive_text()
            try:
                cmd = json.loads(msg)
            except Exception:
                continue
            await _handle_ws_cmd(ws, cmd)
    except WebSocketDisconnect:
        pass
    finally:
        hub.remove_client(ws)


async def _handle_ws_cmd(ws, cmd):
    """处理客户端 WebSocket 指令。"""
    action = cmd.get("action")
    if action == "speak":
        # 手动播报
        text = cmd.get("text", "")
        if text:
            queue.enqueue(text, source="ui", priority=int(cmd.get("priority", 1)), type_="ui")
            await ws.send_text(json.dumps({"event": "ack", "data": {"ok": True}}, ensure_ascii=False))
    elif action == "set_volume":
        tts_service.set_volume(int(cmd.get("volume", 100)))
        await ws.send_text(json.dumps({"event": "ack", "data": {"ok": True, "volume": cmd.get("volume")}}, ensure_ascii=False))
    elif action == "reset_session":
        engine.reset_session()
        await ws.send_text(json.dumps({"event": "ack", "data": {"ok": True}}, ensure_ascii=False))
