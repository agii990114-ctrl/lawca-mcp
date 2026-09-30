"""DB 마이그레이션 러너.

- db/init/ 스크립트가 만드는 기본 스키마(001)는 베이스라인으로 취급한다.
- db/migrations/NNN_*.sql 을 번호순으로 적용하고 schema_migrations에 기록한다(파일 하나 = 트랜잭션 하나).
- 백엔드용 역할(lawca_app, lawca_worker)은 .env의 비밀번호로 만들거나 갱신한다.

사용: python db/migrate.py
"""
import os
import re
import sys
from pathlib import Path

import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parent.parent
env = dict(l.strip().split("=", 1) for l in (ROOT / ".env").read_text().splitlines() if "=" in l)
env.update({k: v for k, v in os.environ.items() if k.startswith("LAWCA_")})
DSN = (f"host={env.get('LAWCA_DB_HOST', '127.0.0.1')} port={env['LAWCA_DB_PORT']} dbname={env['LAWCA_DB_NAME']} "
       f"user=lawca_owner password={env['LAWCA_OWNER_PASSWORD']}")

ROLES = {  # role -> .env 비밀번호 키
    "lawca_app": "LAWCA_APP_PASSWORD",
    "lawca_worker": "LAWCA_WORKER_PASSWORD",
}


def ensure_roles(conn):
    for role, key in ROLES.items():
        pw = env.get(key)
        if not pw:
            sys.exit(f".env에 {key} 가 없습니다.")
        exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone()
        verb = "ALTER" if exists else "CREATE"
        extra = sql.SQL("") if exists else sql.SQL(" NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS")
        conn.execute(sql.SQL("{} ROLE {} LOGIN PASSWORD {}{}").format(
            sql.SQL(verb), sql.Identifier(role), sql.Literal(pw), extra))
    conn.execute("ALTER ROLE lawca_worker SET statement_timeout = '120s'")
    conn.execute("ALTER ROLE lawca_app SET statement_timeout = '30s'")


def main():
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())")
        if conn.execute("SELECT to_regclass('public.matters')").fetchone()[0] and \
                not conn.execute("SELECT 1 FROM schema_migrations WHERE version = '001_baseline'").fetchone():
            conn.execute("INSERT INTO schema_migrations (version) VALUES ('001_baseline')")
        ensure_roles(conn)
        applied = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
        files = sorted((ROOT / "db" / "migrations").glob("*.sql"))
        pending = [f for f in files if f.stem not in applied]
        if not pending:
            print("적용할 마이그레이션 없음 (역할 비밀번호는 갱신됨)")
            return
        for f in pending:
            print(f"적용: {f.name}", flush=True)
            with conn.transaction():
                conn.execute(f.read_text(encoding="utf-8"))
                conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (f.stem,))
        print("완료")


if __name__ == "__main__":
    main()
