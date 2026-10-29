import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from pathlib import Path
from starlette.routing import Mount

from app.api import router
from app.config import settings
from app.database import SessionLocal
from app.workers.runtime import run_maintenance_cycle

logger = logging.getLogger("crm.main")


async def _maintenance_loop():
    """单进程内嵌调度：物化计划 -> 执行作业 -> 派发 Outbox（docs/05 §2）。"""
    while True:
        try:
            await asyncio.to_thread(run_maintenance_cycle, SessionLocal)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - 循环永不退出
            logger.exception("maintenance cycle failed")
        await asyncio.sleep(settings.scheduler_interval_seconds)


def _mount_mcp(app: FastAPI):
    """启动时挂载 MCP（docs/05 §7）。会话管理器一次性的，故每次启动重建。"""
    try:
        from app.mcp_entry import build_mcp_asgi_app
        mcp_asgi = build_mcp_asgi_app()
        cm = mcp_asgi.mcp_starlette.router.lifespan_context(mcp_asgi.mcp_starlette)
    except Exception:  # noqa: BLE001 - 缺 mcp 依赖或初始化失败不拖垮主服务
        logger.exception("MCP entry disabled")
        return None
    app.router.routes = [r for r in app.router.routes
                         if not (isinstance(r, Mount) and r.path == "/mcp")]
    app.mount("/mcp", mcp_asgi)
    logger.info("MCP endpoint mounted at /mcp/")
    return cm


def _unmount_mcp(app: FastAPI, cm) -> None:
    if cm is None:
        return
    app.router.routes = [r for r in app.router.routes
                         if not (isinstance(r, Mount) and r.path == "/mcp")]


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = None
    mcp_cm = None
    if settings.scheduler_enabled:
        logger.info("embedded scheduler enabled (interval=%ss)",
                    settings.scheduler_interval_seconds)
        task = asyncio.create_task(_maintenance_loop())
    if settings.mcp_enabled:
        mcp_cm = _mount_mcp(app)
        if mcp_cm is not None:
            await mcp_cm.__aenter__()
    try:
        yield
    finally:
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        if mcp_cm is not None:
            _unmount_mcp(app, mcp_cm)
            await mcp_cm.__aexit__(None, None, None)


app = FastAPI(
    title="公募基金销售 AI CRM（参考实现）",
    description="多租户 + 销售个人隔离；AI仅负责提案，经人确认后执行。",
    version="0.3.0",
    lifespan=lifespan,
)
app.include_router(router)


@app.get("/", include_in_schema=False)
def frontend():
    path = Path(__file__).resolve().parents[1] / "frontend" / "index.html"
    return FileResponse(path, media_type="text/html", headers={"Cache-Control": "no-store"})
