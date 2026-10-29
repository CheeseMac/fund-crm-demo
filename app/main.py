from fastapi import FastAPI
from fastapi.responses import FileResponse
from pathlib import Path
from app.api import router

app = FastAPI(
    title="公募基金销售 AI CRM（参考实现）",
    description="多租户 + 销售个人隔离；AI仅负责提案，经人确认后执行。",
    version="0.1.0",
)
app.include_router(router)

@app.get("/", include_in_schema=False)
def frontend():
    path = Path(__file__).resolve().parents[1] / "frontend" / "index.html"
    return FileResponse(path, media_type="text/html", headers={"Cache-Control": "no-store"})
