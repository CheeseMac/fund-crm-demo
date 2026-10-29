from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str = "sqlite:///./crm_local.db"
    jwt_secret_key: str = "development-only-secret-must-change-before-production"
    dev_mode: bool = False
    allow_dev_tenant_registration: bool = False
    app_timezone: str = "Asia/Shanghai"
    access_token_minutes: int = 480
    llm_enabled: bool = False
    llm_base_url: str = "http://127.0.0.1:8001/v1"
    llm_api_key: str = ""
    llm_model: str = ""
    # 内置维护循环（计划物化/作业执行/Outbox 派发）；生产可拆独立进程
    scheduler_enabled: bool = True
    scheduler_interval_seconds: int = 30

settings = Settings()

if not settings.dev_mode:
    if len(settings.jwt_secret_key) < 32 or settings.jwt_secret_key.startswith("development-only"):
        raise RuntimeError("Production requires an independent JWT_SECRET_KEY (>=32 chars).")
    if not settings.database_url.startswith("postgresql+psycopg://"):
        raise RuntimeError("Production requires PostgreSQL and application RLS role.")
    if settings.allow_dev_tenant_registration:
        raise RuntimeError("Do not enable dev tenant registration in production.")
