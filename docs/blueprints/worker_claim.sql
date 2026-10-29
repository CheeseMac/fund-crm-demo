-- PostgreSQL reference only: adapt to your Alembic schema and application RLS policies.
-- A dedicated internal job role may claim job metadata but MUST NOT receive general
-- unrestricted access to all customers. Never use a normal public API user's token.
--
-- Assumes a job_runs table with:
-- id, status, available_at, lease_until, attempts, worker_id, idempotency_key.
-- This query MUST run within a transaction; commit promptly before doing long work.

WITH picked AS (
    SELECT id
    FROM job_runs
    WHERE (
        (status = 'queued' AND available_at <= now())
        OR (status = 'running' AND lease_until < now())
    )
    ORDER BY available_at ASC, id ASC
    FOR UPDATE SKIP LOCKED
    LIMIT 20
)
UPDATE job_runs AS j
SET status = 'running',
    worker_id = :worker_id,
    attempts = attempts + 1,
    lease_until = now() + interval '5 minutes'
FROM picked
WHERE j.id = picked.id
RETURNING j.id, j.idempotency_key, j.attempts;

-- Scheduled logical occurrences must be unique (enforce via migration):
-- CREATE UNIQUE INDEX ... ON job_runs(schedule_id, scheduled_for)
--   WHERE schedule_id IS NOT NULL;
--
-- Worker must revalidate the persisted job tenant/user scope before business queries,
-- and all side effects must be idempotent. Lease expiration can cause a repeat attempt.
--
-- For alert generation, use durable uniqueness. A prior SELECT is not enough
-- in the presence of concurrent workers.
--
-- INSERT INTO alerts (...) VALUES (...)
-- ON CONFLICT (tenant_id, owner_user_id, dedup_key) DO NOTHING;
--
-- A notification_delivery should have an independent idempotency key that
-- incorporates alert_id, channel, recipient, and delivery_policy_version.
