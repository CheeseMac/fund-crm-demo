"""单进程维护循环：物化计划 -> 执行作业 -> 派发 Outbox。

演示环境由 API 进程内嵌运行（SCHEDULER_ENABLED，默认开）；
生产可独立为 crm-scheduler / crm-worker 进程，复用同一组函数。
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import sessionmaker

from app.workers.scheduling import materialize_due_schedules
from app.workers.runner import run_due_jobs
from app.workers.outbox import drain

logger = logging.getLogger("crm.workers")


def run_maintenance_cycle(factory: sessionmaker) -> dict:
    materialized = 0
    with factory() as db:
        materialized = materialize_due_schedules(db)
    jobs = run_due_jobs(factory)
    outbox_processed = drain(factory)
    summary = {"materialized": materialized,
               "jobs": [{"id": j["id"], "status": j["status"]} for j in jobs],
               "outbox_processed": outbox_processed}
    if jobs or outbox_processed or materialized:
        logger.info("maintenance cycle: %s", summary)
    return summary
