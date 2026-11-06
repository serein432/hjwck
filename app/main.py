# -*- coding: utf-8 -*-
"""
应用入口：FastAPI 应用工厂 + 启动编排。
- 挂载路由（统一响应/鉴权/限流）
- 启动触发源（定时/MQTT/DB轮询/传感器）
- 暴露 /api 前缀（文档接口）与根路径（供 App 壳调试）
"""
import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config.settings import settings, BASE_DIR
from app.logging import app_logger as lg
from app.api import routes
from app.trigger import triggers


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.get("system", "name"),
        version=settings.get("system", "version"),
        docs_url="/docs",
    )
    # CORS（App 壳/本地跨域）
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # 挂载业务路由
    app.include_router(routes.router)

    # 启动事件：初始化触发源
    @app.on_event("startup")
    def _startup():
        lg.log("info", "main", "服务启动", version=settings.get("system", "version"),
               mode=settings.get("system", "mode"))
        try:
            triggers.start_all_triggers()
        except Exception as e:
            lg.log("error", "main", "触发源启动异常", err=str(e))

    @app.on_event("shutdown")
    def _shutdown():
        lg.log("info", "main", "服务关闭")

    return app


app = create_app()
