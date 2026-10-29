"""Operator-only bootstrap script. Run with MIGRATION_DATABASE_URL / owner credentials.

Example:
  python -m scripts.create_tenant --slug east-desk --name '东方销售部' \
    --email admin@example.test
Password is read from an interactive, non-echoing prompt.
"""
import argparse
import getpass
from sqlalchemy import select
from app.database import SessionLocal
from app.models import Tenant, User, uid
from app.security import hash_password

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--slug", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--email", required=True)
    args = parser.parse_args()
    password = getpass.getpass("Admin password (>=12 chars): ")
    if len(password) < 12:
        raise ValueError("Password must be at least 12 characters")
    tid, admin_id = uid(), uid()
    with SessionLocal() as db:
        db.info.update(tenant_id=tid, user_id=admin_id)
        if db.scalar(select(Tenant.id).where(Tenant.slug == args.slug)):
            raise ValueError("Tenant slug already exists")
        db.add(Tenant(id=tid, name=args.name, slug=args.slug))
        db.add(User(id=admin_id, tenant_id=tid, email=args.email.lower(),
                    password_hash=hash_password(password), role="admin"))
        db.commit()
    print(f"Created tenant {args.slug} and admin {args.email.lower()}")

if __name__ == "__main__":
    main()
