"""DB 접근: 읽기 전용 계정 + 행 수준 보안(RLS) 컨텍스트 + 감사 로그"""
import os
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

ROOT = Path(__file__).resolve().parent.parent


def _env():
    env = {}
    f = ROOT / ".env"
    if f.exists():
        env = dict(l.strip().split("=", 1) for l in f.read_text().splitlines() if "=" in l)
    env.update({k: v for k, v in os.environ.items() if k.startswith("LAWCA_")})
    return env


ENV = _env()
_BASE = f"host={ENV.get('LAWCA_DB_HOST', '127.0.0.1')} port={ENV['LAWCA_DB_PORT']} dbname={ENV['LAWCA_DB_NAME']}"
READER_DSN = _BASE + f" user=lawca_reader password={ENV['LAWCA_READER_PASSWORD']}"
AUDIT_DSN = _BASE + f" user=lawca_audit password={ENV['LAWCA_AUDIT_PASSWORD']}"


class AuthError(Exception):
    pass


def current_user_id() -> str:
    """이 서버 프로세스가 대변하는 사용자. 프로토타입에서는 환경변수(LAWCA_USER_ID)로 주입한다.
    실제 배포에서는 인증 토큰에서 얻어야 한다. 미설정이면 fail closed."""
    uid = os.environ.get("LAWCA_USER_ID", "").strip()
    if not uid:
        raise AuthError("사용자가 지정되지 않았습니다(LAWCA_USER_ID). 접근을 거부합니다.")
    return uid


def reader(user_id: str):
    """RLS가 적용된 읽기 전용 연결. app.user_id를 세션에 설정한다."""
    conn = psycopg.connect(READER_DSN, autocommit=True, row_factory=dict_row)
    conn.execute("SELECT set_config('app.user_id', %s, false)", (user_id,))
    row = conn.execute("SELECT name, role FROM users WHERE user_id = %s", (user_id,)).fetchone()
    if row is None:
        conn.close()
        raise AuthError(f"등록되지 않은 사용자입니다: {user_id}")
    return conn


def audit(user_id: str, tool: str, args: dict, doc_ids: list[str], result_count: int) -> None:
    """감사 로그 기록. 실패하면 예외를 그대로 올려 도구 호출을 실패시킨다(기록 없이 조회되지 않게)."""
    with psycopg.connect(AUDIT_DSN, autocommit=True) as c:
        c.execute(
            "INSERT INTO audit_log (user_id, tool, args, doc_ids, result_count) VALUES (%s,%s,%s,%s,%s)",
            (user_id, tool, Jsonb(args), doc_ids, result_count),
        )
