"""MCP 접속 토큰: 발급·목록·폐기, 권한, 해시 저장, DB 검증 함수의 무효 조건"""
import hashlib

import psycopg

from backend.app import config
from backend.app.permissions import PERMISSIONS


def verify(raw: str):
    """MCP 서버와 같은 경로: reader 계정이 검증 함수만 호출한다."""
    dsn = (f"host={config.ENV.get('LAWCA_DB_HOST', '127.0.0.1')} port={config.ENV['LAWCA_DB_PORT']} dbname={config.ENV['LAWCA_DB_NAME']}"
           f" user=lawca_reader password={config.ENV['LAWCA_READER_PASSWORD']}")
    with psycopg.connect(dsn, autocommit=True) as c:
        return c.execute("SELECT token_id, user_id, role FROM verify_api_token(%s)", (hashlib.sha256(raw.encode()).hexdigest(),)).fetchone()


def test_only_lawyers_can_issue_tokens(login):
    assert login("t_clerk_a").post("/api/tokens", json={"name": "x"}).status_code == 403
    assert login("t_admin").post("/api/tokens", json={"name": "x"}).status_code == 403
    assert login("t_lawyer_a").post("/api/tokens", json={"name": "내 Claude"}).status_code == 201
    assert login("t_clerk_a").get("/api/tokens").status_code == 403


def test_token_is_shown_once_and_only_the_hash_is_stored(login, owner):
    r = login("t_lawyer_b").post("/api/tokens", json={"name": "노트북", "days": 7})
    body = r.json()
    raw = body["token"]
    assert raw.startswith("lawca_") and len(raw) > 40
    row = owner.execute("SELECT token_hash, prefix FROM api_tokens WHERE token_id = %s", (body["token_id"],)).fetchone()
    assert row["token_hash"] == hashlib.sha256(raw.encode()).hexdigest() and raw not in str(row)
    listed = login("t_lawyer_b").get("/api/tokens").json()
    assert all("token" not in t and "token_hash" not in t for t in listed)            # 목록에는 원문·해시가 없다
    assert next(t for t in listed if t["token_id"] == body["token_id"])["prefix"] == raw[:10]


def test_db_verification_function_and_invalid_cases(login, owner):
    api = login("t_lawyer_a")
    raw = api.post("/api/tokens", json={"name": "검증용", "days": 1}).json()
    tok = raw["token"]
    assert tuple(verify(tok))[1:] == ("t_lawyer_a", "변호사")
    assert verify("lawca_not-a-real-token") is None
    # 만료
    owner.execute("UPDATE api_tokens SET expires_at = now() - interval '1 second' WHERE token_id = %s", (raw["token_id"],))
    assert verify(tok) is None
    owner.execute("UPDATE api_tokens SET expires_at = now() + interval '1 day' WHERE token_id = %s", (raw["token_id"],))
    assert verify(tok) is not None
    # 사용자 비활성화
    owner.execute("UPDATE users SET active = false WHERE user_id = 't_lawyer_a'")
    try:
        assert verify(tok) is None
    finally:
        owner.execute("UPDATE users SET active = true WHERE user_id = 't_lawyer_a'")
    # 폐기
    assert api.delete(f"/api/tokens/{raw['token_id']}").status_code == 200
    assert verify(tok) is None
    assert api.delete(f"/api/tokens/{raw['token_id']}").status_code == 404           # 이미 폐기됨


def test_cannot_revoke_someone_elses_token_and_limit_of_active_tokens(login):
    a, b = login("t_lawyer_a"), login("t_lawyer_b")
    tid = a.post("/api/tokens", json={"name": "남의 것"}).json()["token_id"]
    assert b.delete(f"/api/tokens/{tid}").status_code == 404
    assert tid not in [t["token_id"] for t in b.get("/api/tokens").json()]
    # 활성 토큰은 사용자당 5개까지
    for t in a.get("/api/tokens").json():
        if t["revoked_at"] is None:
            a.delete(f"/api/tokens/{t['token_id']}")
    for i in range(5):
        assert a.post("/api/tokens", json={"name": f"t{i}"}).status_code == 201
    assert a.post("/api/tokens", json={"name": "여섯째"}).status_code == 409


def test_token_validation_rules(login):
    a = login("t_lawyer_a")
    assert a.post("/api/tokens", json={"name": "", "days": 5}).status_code == 422
    assert a.post("/api/tokens", json={"name": "x", "days": 365}).status_code == 422       # 최대 90일
    assert a.post("/api/tokens", json={"name": "x", "days": 0}).status_code == 422


def test_admin_deactivation_and_password_reset_revoke_tokens(login, owner):
    """전용 사용자로 시험한다(공용 시험 계정의 비밀번호를 바꾸면 다른 시험이 로그인하지 못한다)."""
    import secrets
    from backend.app import security
    from backend.tests.helpers import Api
    pw = secrets.token_urlsafe(12) + "Aa1"
    owner.execute("INSERT INTO users (user_id, name, role) VALUES ('t_tokvictim','토큰시험','변호사')")
    owner.execute("INSERT INTO user_credentials (user_id, password_hash) VALUES ('t_tokvictim', %s)", (security.hash_password(pw),))
    a = login("t_admin")
    victim = Api()
    assert victim.login("t_tokvictim", pw).status_code == 200
    tok = victim.post("/api/tokens", json={"name": "곧 폐기"}).json()["token"]
    assert verify(tok) is not None
    assert a.post("/api/admin/users/t_tokvictim/reset-password").status_code == 200
    assert verify(tok) is None                                                           # 비밀번호 초기화 시 토큰도 폐기
    tok2_owner = owner.execute("SELECT count(*) AS n FROM api_tokens WHERE user_id = 't_tokvictim' AND revoked_at IS NULL").fetchone()["n"]
    assert tok2_owner == 0


def test_token_permission_matches_the_role_table():
    """MCP 서버(server/http_auth.py)가 허용하는 역할 목록과 권한표가 어긋나지 않는다."""
    roles = {r for r, perms in PERMISSIONS.items() if "mcp.token" in perms}
    assert roles == {"변호사"}
