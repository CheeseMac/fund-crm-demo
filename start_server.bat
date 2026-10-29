@echo off
cd /d %~dp0
if not exist crm_demo.db (
  echo Initializing database...
  .venv\Scripts\python.exe -m alembic upgrade head
  .venv\Scripts\python.exe -m scripts.seed_demo
)
echo CRM demo running at http://127.0.0.1:8000/  (Ctrl+C to stop)
start "" cmd /c "timeout /t 3 >nul & start http://127.0.0.1:8000/"
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
