"""DB 연결. API는 lawca_app 계정으로 접속하고, 요청마다 트랜잭션 안에서 app.user_id를 설정한다.

set_config(..., true)는 트랜잭션 로컬이므로 풀 연결로 사용자 신원이 새지 않는다.
"""
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from . import config

pool = ConnectionPool(config.APP_DSN, min_size=1, max_size=10, kwargs={"row_factory": dict_row}, open=False)


def open_pool():
    pool.open()


def close_pool():
    pool.close()


@contextmanager
def tx(user_id: str | None = None):
    """한 요청 = 한 트랜잭션. user_id가 있으면 RLS 컨텍스트를 설정한다."""
    with pool.connection() as conn:
        with conn.transaction():
            if user_id:
                conn.execute("SELECT set_config('app.user_id', %s, true)", (user_id,))
            yield conn


def audit(user_id: str, action: str, args: dict | None = None, doc_ids: list[str] | None = None, count: int | None = None):
    """감사 로그(전용 계정, INSERT만). API 동작은 tool='api:<action>' 으로 기록한다."""
    with psycopg.connect(config.AUDIT_DSN, autocommit=True) as c:
        c.execute("INSERT INTO audit_log (user_id, tool, args, doc_ids, result_count) VALUES (%s,%s,%s,%s,%s)",
                  (user_id, f"api:{action}", Jsonb(args or {}), doc_ids or [], count))
