import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from pathlib import Path

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


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = None
    if settings.scheduler_enabled:
        logger.info("embedded scheduler enabled (interval=%ss)",
                    settings.scheduler_interval_seconds)
        task = asyncio.create_task(_maintenance_loop())
    yield
    if task is not None:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(
    title="公募基金销售 AI CRM（参考实现）",
    description="多租户 + 销售个人隔离；AI仅负责提案，经人确认后执行。",
    version="0.2.0",
    lifespan=lifespan,
)
app.include_router(router)

@app.get("/", include_in_schema=False)
def frontend():
    path = Path(__file__).resolve().parents[1] / "frontend" / "index.html"
    return FileResponse(path, media_type="text/html", headers={"Cache-Control": "no-store"})
