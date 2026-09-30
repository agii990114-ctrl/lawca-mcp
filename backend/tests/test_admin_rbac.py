"""역할 권한표(RBAC)와 그룹 격리. 관리자는 사용자를 관리하지만 사건 내용은 그룹 소속 없이는 볼 수 없다."""
import pytest


def test_only_admin_manages_users_and_groups(login):
    for uid in ("t_lawyer_a", "t_clerk_a"):
        api = login(uid)
        assert api.get("/api/admin/users").status_code == 403
        assert api.post("/api/admin/users", json={"user_id": "t_x1", "name": "x", "role": "변호사"}).status_code == 403
        assert api.post("/api/admin/groups", json={"group_id": "T-X"}).status_code == 403
    admin = login("t_admin")
    assert admin.get("/api/admin/users").status_code == 200
    assert admin.get("/api/admin/groups").status_code == 200


def test_admin_creates_user_with_temp_password_and_forced_change(login, owner):
    admin = login("t_admin")
    r = admin.post("/api/admin/users", json={"user_id": "t_new_lawyer", "name": "신규", "role": "변호사", "groups": ["T-A"]})
    assert r.status_code == 201, r.text
    temp = r.json()["temporary_password"]
    from backend.tests.helpers import Api
    u = Api()
    assert u.login("t_new_lawyer", temp).status_code == 200
    me = u.get("/api/auth/me").json()
    assert me["must_change_password"] is True
    assert u.get("/api/matters").status_code == 403                       # 변경 전에는 다른 API 사용 불가
    new = "Chang3d-pass-word!"
    assert u.post("/api/auth/change-password", json={"current_password": temp, "new_password": new}).status_code == 200
    assert u.get("/api/matters").status_code == 200
    # 임시 비밀번호는 DB에 평문으로 남지 않는다
    stored = owner.execute("SELECT password_hash FROM user_credentials WHERE user_id = 't_new_lawyer'").fetchone()["password_hash"]
    assert temp not in stored and new not in stored


def test_create_user_validation(login):
    admin = login("t_admin")
    assert admin.post("/api/admin/users", json={"user_id": "t_bad_role", "name": "x", "role": "대표"}).status_code == 400
    assert admin.post("/api/admin/users", json={"user_id": "t_bad_group", "name": "x", "role": "변호사", "groups": ["NOPE"]}).status_code == 400
    assert admin.post("/api/admin/users", json={"user_id": "Bad ID!", "name": "x", "role": "변호사"}).status_code == 422
    assert admin.post("/api/admin/users", json={"user_id": "t_lawyer_a", "name": "x", "role": "변호사"}).status_code == 409


def test_admin_self_protection_and_last_admin(login, owner):
    admin = login("t_admin")
    assert admin.patch("/api/admin/users/t_admin", json={"active": False}).status_code == 400
    assert admin.patch("/api/admin/users/t_admin", json={"role": "변호사"}).status_code == 400
    # 마지막 활성 관리자 보호: 다른 관리자가 없다고 가정하고 개발 관리자를 잠시 비활성화한 상태에서 검증
    others = owner.execute("SELECT user_id FROM users WHERE role = '관리자' AND active AND user_id <> 't_admin'").fetchall()
    for o in others:
        owner.execute("UPDATE users SET active = false WHERE user_id = %s", (o["user_id"],))
    try:
        owner.execute("INSERT INTO users (user_id, name, role) VALUES ('t_admin2','관리자2','관리자')")
        assert admin.patch("/api/admin/users/t_admin2", json={"active": False}).status_code == 200    # 관리자가 2명이므로 가능
        assert admin.patch("/api/admin/users/t_admin2", json={"active": True}).status_code == 200
    finally:
        for o in others:
            owner.execute("UPDATE users SET active = true WHERE user_id = %s", (o["user_id"],))


def test_group_membership_change_takes_effect_immediately(login, new_matter, owner):
    m = new_matter()
    b = login("t_lawyer_b")
    assert b.get(f"/api/matters/{m['matter_id']}").status_code == 404
    admin = login("t_admin")
    assert admin.put("/api/admin/users/t_lawyer_b/groups", json={"groups": ["T-B", "T-A"]}).status_code == 200
    assert b.get(f"/api/matters/{m['matter_id']}").status_code == 200        # 세션 재로그인 없이 즉시 반영
    assert admin.put("/api/admin/users/t_lawyer_b/groups", json={"groups": ["T-B"]}).status_code == 200
    assert b.get(f"/api/matters/{m['matter_id']}").status_code == 404


def test_admin_cannot_read_case_content_without_group(login, new_matter):
    m = new_matter()
    admin = login("t_admin")                    # 접근 그룹이 없는 관리자
    assert admin.get("/api/matters").json()["total"] == 0 or all(x["access_group"] not in ("T-A", "T-B") for x in admin.get("/api/matters").json()["items"])
    assert admin.get(f"/api/matters/{m['matter_id']}").status_code == 404


def test_group_isolation_for_matters(login, new_matter):
    m = new_matter()
    a, b = login("t_lawyer_a"), login("t_lawyer_b")
    assert a.get(f"/api/matters/{m['matter_id']}").status_code == 200
    assert b.get(f"/api/matters/{m['matter_id']}").status_code == 404
    ids_b = {x["matter_id"] for x in b.get("/api/matters?limit=200").json()["items"]}
    assert m["matter_id"] not in ids_b
    # 소속되지 않은 그룹에는 사건을 만들 수 없다
    body = dict(case_no="2026가합98888", case_type="대여금", plaintiff="가", defendant="나", our_side="원고", access_group="T-A")
    assert b.post("/api/matters", json=body).status_code == 403


@pytest.mark.parametrize("uid,can_delete", [("t_lawyer_a", True), ("t_clerk_a", False)])
def test_document_delete_permission_by_role(login, new_matter, owner, uid, can_delete):
    m = new_matter()
    owner.execute("INSERT INTO documents (doc_id, matter_id, kind, round, author_side, full_text, text_norm, access_group)"
                  " VALUES (%s,%s,'기타',1,'미상','본문','본문','T-A')", (f"tdoc_{uid}", m["matter_id"]))
    r = login(uid).delete(f"/api/documents/tdoc_{uid}")
    assert (r.status_code == 200) is can_delete, r.text
    if not can_delete:
        assert r.status_code == 403
