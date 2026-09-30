"""DB 접근: 읽기 전용 계정 + 행 수준 보안(RLS) 컨텍스트 + 감사 로그 + MCP 토큰 검증

사용자 신원:
- HTTP 모드(운영): 요청마다 인증 미들웨어가 Bearer 토큰을 검증해 (user_id, token_id)를 요청 컨텍스트에 넣는다.
  이 모드에서는 환경변수 신원을 절대 쓰지 않는다.
- stdio 모드(개발): 서버를 띄우는 프로세스의 LAWCA_USER_ID. 개인 PC 시험용이며 신원을 마음대로 정할 수 있으므로 운영에 쓰지 않는다.
"""
import contextvars
import hashlib
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
        env = dict(l.strip().split("=", 1) for l in f.read_text().splitlines() if "=" in l and not l.startswith("#"))
    env.update({k: v for k, v in os.environ.items() if k.startswith("LAWCA_")})
    return env


ENV = _env()
_BASE = f"host={ENV.get('LAWCA_DB_HOST', '127.0.0.1')} port={ENV['LAWCA_DB_PORT']} dbname={ENV['LAWCA_DB_NAME']}"
READER_DSN = _BASE + f" user=lawca_reader password={ENV['LAWCA_READER_PASSWORD']}"
AUDIT_DSN = _BASE + f" user=lawca_audit password={ENV['LAWCA_AUDIT_PASSWORD']}"

HTTP_MODE = False
_request = contextvars.ContextVar("lawca_request", default=None)     # (user_id, token_id)


class AuthError(Exception):
    pass


def set_http_mode() -> None:
    global HTTP_MODE
    HTTP_MODE = True


def bind_request(user_id: str, token_id: int):
    return _request.set((user_id, token_id))


def reset_request(token) -> None:
    _request.reset(token)


def current_user_id() -> str:
    ctx = _request.get()
    if ctx:
        return ctx[0]
    if HTTP_MODE:
        raise AuthError("인증되지 않은 요청입니다.")            # HTTP 모드에서는 환경변수로 신원을 대신하지 않는다(fail closed)
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
    ctx = _request.get()
    if ctx:                       # 어떤 토큰으로 호출했는지도 남긴다(유출 시 추적)
        args = {**args, "via": "mcp-http", "token_id": ctx[1]}
    with psycopg.connect(AUDIT_DSN, autocommit=True) as c:
        c.execute(
            "INSERT INTO audit_log (user_id, tool, args, doc_ids, result_count) VALUES (%s,%s,%s,%s,%s)",
            (user_id, tool, Jsonb(args), doc_ids, result_count),
        )


# ---- MCP 토큰 검증 (읽기 전용 계정은 api_tokens 를 직접 읽지 못하고 검증 함수만 호출한다)
def verify_token(raw: str) -> dict | None:
    with psycopg.connect(READER_DSN, autocommit=True, row_factory=dict_row) as c:
        return c.execute("SELECT token_id, user_id, role FROM verify_api_token(%s)", (hashlib.sha256(raw.encode()).hexdigest(),)).fetchone()


def touch_token(token_id: int) -> None:
    with psycopg.connect(AUDIT_DSN, autocommit=True) as c:
        c.execute("SELECT touch_api_token(%s)", (token_id,))
