import secrets

from backend.app import security
from backend.tests.helpers import Api


def test_login_me_logout(login):
    api = login("t_lawyer_a")
    me = api.get("/api/auth/me").json()
    assert me["user_id"] == "t_lawyer_a" and me["role"] == "변호사" and me["groups"] == ["T-A"]
    assert "documents.upload" in me["permissions"] and "users.manage" not in me["permissions"]
    assert me["csrf_token"] == api.csrf                       # 새로고침 후 복구용
    assert api.post("/api/auth/logout").status_code == 200
    assert api.get("/api/auth/me").status_code == 401


def test_cookie_flags(passwords):
    r = Api().login("t_lawyer_a", passwords["t_lawyer_a"])
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie and "path=/" in cookie


def test_wrong_password_and_unknown_user_look_identical(passwords):
    a = Api().login("t_lawyer_a", "definitely-wrong-1A")
    b = Api().login("t_no_such_user", "definitely-wrong-1A")
    assert a.status_code == b.status_code == 401
    assert a.json() == b.json()


def test_lockout_after_repeated_failures(owner, passwords):
    owner.execute("INSERT INTO users (user_id, name, role) VALUES ('t_lock','잠금테스트','변호사')")
    pw = secrets.token_urlsafe(12) + "Aa1"
    owner.execute("INSERT INTO user_credentials (user_id, password_hash) VALUES ('t_lock', %s)", (security.hash_password(pw),))
    api = Api()
    for _ in range(5):
        assert api.login("t_lock", "wrong-password-1A").status_code == 401
    r = api.login("t_lock", pw)          # 올바른 비밀번호라도 잠금 중에는 거부
    assert r.status_code == 429
    # 잠금은 시도 기록 기준이라 다른 사용자에는 영향이 없다
    assert Api().login("t_lawyer_a", passwords["t_lawyer_a"]).status_code == 200


def test_csrf_required_for_state_changes(login):
    api = login("t_lawyer_a")
    api.csrf = None
    assert api.post("/api/auth/logout").status_code == 403
    api.csrf = "wrong-token"
    assert api.post("/api/auth/logout").status_code == 403


def test_unauthenticated_requests_rejected():
    api = Api()
    for path in ("/api/matters", "/api/documents", "/api/jobs", "/api/admin/users"):
        assert api.get(path).status_code == 401, path


def test_deactivated_user_session_is_invalid(login, owner):
    owner.execute("INSERT INTO users (user_id, name, role) VALUES ('t_temp','임시','사무원') ON CONFLICT DO NOTHING")
    pw = secrets.token_urlsafe(12) + "Aa1"
    owner.execute("INSERT INTO user_credentials (user_id, password_hash) VALUES ('t_temp', %s)"
                  " ON CONFLICT (user_id) DO UPDATE SET password_hash = EXCLUDED.password_hash", (security.hash_password(pw),))
    api = Api()
    assert api.login("t_temp", pw).status_code == 200
    assert api.get("/api/auth/me").status_code == 200
    owner.execute("UPDATE users SET active = false WHERE user_id = 't_temp'")
    assert api.get("/api/auth/me").status_code == 401
    assert Api().login("t_temp", pw).status_code == 401      # 비활성 사용자는 새로 로그인도 못 한다


def test_expired_session_rejected_and_removed(login, owner):
    api = login("t_lawyer_a")
    th = security.token_hash(api.c.cookies.get("lawca_session"))
    owner.execute("UPDATE sessions SET expires_at = now() - interval '1 minute' WHERE token_hash = %s", (th,))
    assert api.get("/api/auth/me").status_code == 401
    assert owner.execute("SELECT count(*) AS n FROM sessions WHERE token_hash = %s", (th,)).fetchone()["n"] == 0   # 만료 세션은 삭제된다


def test_idle_session_rejected(login, owner):
    api = login("t_lawyer_b")
    owner.execute("UPDATE sessions SET last_seen = now() - interval '3 hours' WHERE user_id = 't_lawyer_b'")
    assert api.get("/api/auth/me").status_code == 401


def test_change_password_rules_and_other_sessions_revoked(passwords, login):
    a1, a2 = login("t_clerk_a"), login("t_clerk_a")
    cur = passwords["t_clerk_a"]
    assert a1.post("/api/auth/change-password", json={"current_password": cur, "new_password": "short1A"}).status_code == 400
    assert a1.post("/api/auth/change-password", json={"current_password": cur, "new_password": "aaaaaaaaaaaa"}).status_code == 400
    assert a1.post("/api/auth/change-password", json={"current_password": "nope-nope-1A", "new_password": "Str0ng-new-pass"}).status_code == 400
    new = "Str0ng-new-pass-" + secrets.token_hex(3)
    assert a1.post("/api/auth/change-password", json={"current_password": cur, "new_password": new}).status_code == 200
    assert a1.get("/api/auth/me").status_code == 200          # 현재 세션은 유지
    assert a2.get("/api/auth/me").status_code == 401          # 다른 세션은 종료
    assert Api().login("t_clerk_a", cur).status_code == 401
    assert Api().login("t_clerk_a", new).status_code == 200
    passwords["t_clerk_a"] = new


def test_password_hash_properties():
    h1, h2 = security.hash_password("Correct-horse-1"), security.hash_password("Correct-horse-1")
    assert h1 != h2 and h1.startswith("scrypt$")            # 솔트가 달라야 한다
    assert security.verify_password("Correct-horse-1", h1) and not security.verify_password("Correct-horse-2", h1)
    assert not security.verify_password("anything", None)   # 없는 사용자도 예외 없이 거부
    assert "Correct-horse-1" not in h1
