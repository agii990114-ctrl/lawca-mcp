"""테스트 공통: 전용 접근 그룹(T-A, T-B)과 t_ 접두 사용자를 만들고, 끝나면 모두 지운다. 개발용 더미 데이터는 건드리지 않는다."""
import itertools
import shutil
import secrets

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from backend.app import config, security
from backend.app.ingest import storage
from backend.app.main import app
from backend.tests.helpers import Api

GROUPS = ["T-A", "T-B"]
_CASE_COUNTER = itertools.count(90001)     # 세션 전체에서 유일한 사건번호
USERS = {  # user_id: (역할, 접근 그룹)
    "t_admin": ("관리자", []),
    "t_lawyer_a": ("변호사", ["T-A"]),
    "t_clerk_a": ("사무원", ["T-A"]),
    "t_lawyer_b": ("변호사", ["T-B"]),
}


def _cleanup(o):
    o.execute("DELETE FROM documents WHERE access_group = ANY(%s)", (GROUPS,))
    o.execute("DELETE FROM ingest_batches WHERE access_group = ANY(%s)", (GROUPS,))
    o.execute("DELETE FROM uploaded_files WHERE access_group = ANY(%s)", (GROUPS,))
    o.execute("DELETE FROM matters WHERE access_group = ANY(%s)", (GROUPS,))
    o.execute("DELETE FROM sessions WHERE user_id LIKE 't\\_%%'")
    o.execute("DELETE FROM login_attempts WHERE user_id LIKE 't\\_%%'")
    o.execute("DELETE FROM audit_log WHERE user_id LIKE 't\\_%%'")
    o.execute("DELETE FROM user_groups WHERE user_id LIKE 't\\_%%' OR group_id = ANY(%s)", (GROUPS,))
    o.execute("DELETE FROM users WHERE user_id LIKE 't\\_%%'")
    o.execute("DELETE FROM access_groups WHERE group_id = ANY(%s)", (GROUPS,))
    for g in GROUPS:
        shutil.rmtree(storage.BLOBS / g, ignore_errors=True)


@pytest.fixture(scope="session")
def owner():
    o = psycopg.connect(config.OWNER_DSN, autocommit=True, row_factory=dict_row)
    yield o
    o.close()


@pytest.fixture(scope="session")
def passwords(owner):
    _cleanup(owner)
    for g in GROUPS:
        owner.execute("INSERT INTO access_groups VALUES (%s,%s)", (g, "테스트 그룹"))
    pws = {}
    for uid, (role, groups) in USERS.items():
        pws[uid] = secrets.token_urlsafe(12) + "Aa1"
        owner.execute("INSERT INTO users (user_id, name, role) VALUES (%s,%s,%s)", (uid, f"테스트-{uid}", role))
        owner.execute("INSERT INTO user_credentials (user_id, password_hash, must_change) VALUES (%s,%s,false)",
                      (uid, security.hash_password(pws[uid])))
        for g in groups:
            owner.execute("INSERT INTO user_groups VALUES (%s,%s)", (uid, g))
    yield pws
    _cleanup(owner)


@pytest.fixture(scope="session", autouse=True)
def _app_lifespan():
    with TestClient(app):     # 풀 열기/닫기(lifespan)
        yield


@pytest.fixture
def login(passwords):
    def _login(uid):
        api = Api()
        r = api.login(uid, passwords[uid])
        assert r.status_code == 200, r.text
        return api
    return _login


@pytest.fixture
def new_matter(login):
    """T-A 그룹에 사건을 만든다(테스트마다 고유 사건번호)."""
    def _make(case_no=None, group="T-A", user="t_lawyer_a", **kw):
        api = login(user)
        body = dict(case_no=case_no or f"2026가합{next(_CASE_COUNTER)}", case_type="대여금", plaintiff="테스트갑", defendant="테스트을",
                    our_side="피고", access_group=group)
        body.update(kw)
        r = api.post("/api/matters", json=body)
        assert r.status_code == 201, r.text
        return r.json()
    return _make
