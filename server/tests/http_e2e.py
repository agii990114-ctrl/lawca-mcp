"""MCP HTTP 종단 테스트(컨테이너 스택, 프록시 경유): 웹에서 발급한 토큰으로 실제 MCP 클라이언트가 접속한다.

  server/.venv/Scripts/python server/tests/http_e2e.py [https://localhost:8443]

확인: 토큰 인증(없음·잘못됨·폐기·만료·비활성 사용자), 신원 위조 불가, 접근 그룹 격리, 마스킹, 감사 로그(토큰 기록), 호출 속도 제한.
"""
import asyncio
import json
import sys
import time
from pathlib import Path

import httpx
import httpx2
import psycopg
from mcp.client.client import Client
from mcp.client.streamable_http import streamable_http_client

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "server"))
import db  # noqa: E402

BASE = sys.argv[1] if len(sys.argv) > 1 else "https://localhost:8443"
MCP_URL = BASE + "/mcp"
OWNER = db._BASE + f" user=lawca_owner password={db.ENV['LAWCA_OWNER_PASSWORD']}"
creds = {l.split("\t")[0]: l.split("\t")[2].strip() for l in (ROOT / ".dev-credentials.txt").read_text(encoding="utf-8").splitlines() if l and l[0] != "#"}
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name, str(detail)[:150], flush=True)


def web_login(uid):
    c = httpx.Client(base_url=BASE, verify=False, timeout=60)
    r = c.post("/api/auth/login", json={"user_id": uid, "password": creds[uid]})
    assert r.status_code == 200, r.text
    return c, {"X-CSRF-Token": r.json()["csrf_token"]}


def new_token(uid, name="http-e2e", days=1):
    c, h = web_login(uid)
    r = c.post("/api/tokens", headers=h, json={"name": name, "days": days})
    return r, c, h


def mcp_client(token, extra_headers=None):
    hc = httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}", **(extra_headers or {})}, verify=False, timeout=120)
    return Client(streamable_http_client(MCP_URL, http_client=hc), read_timeout_seconds=180)


async def call(c, tool, args=None):
    r = await c.call_tool(tool, args or {})
    return r.structured_content or json.loads(r.content[0].text)


def raw_post(token, headers=None):
    h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream", **(headers or {})}
    if token is not None:
        h["Authorization"] = f"Bearer {token}"
    return httpx.post(MCP_URL, headers=h, verify=False, timeout=30, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})


async def main():
    o = psycopg.connect(OWNER, autocommit=True)
    r, kim_web, kim_h = new_token("u_kim")
    check("변호사(u_kim)는 토큰 발급 가능", r.status_code == 201, r.status_code)
    kim = r.json()
    r2, park_web, park_h = new_token("u_park")
    park = r2.json()
    rc, _, _ = new_token("u_clerk")
    check("사무원(u_clerk)은 토큰 발급 불가", rc.status_code == 403, rc.status_code)

    # --- 인증
    a = raw_post(None); b = raw_post("lawca_not-a-token")
    check("토큰 없음 → 401(+WWW-Authenticate)", a.status_code == 401 and "Bearer" in a.headers.get("www-authenticate", ""), a.status_code)
    check("잘못된 토큰 → 401", b.status_code == 401, b.status_code)
    check("토큰 없음과 잘못된 토큰은 응답 형식이 같다(존재 여부 비노출)", a.json().get("error") == b.json().get("error") == "invalid_token")
    check("기본 인증 방식(Basic)은 받지 않는다", httpx.post(MCP_URL, headers={"Authorization": "Basic dV9raW06eA==", "Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
                                                   verify=False, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).status_code == 401)

    # --- u_kim 토큰으로 실제 MCP 사용
    async with mcp_client(kim["token"]) as c:
        tools = sorted(t.name for t in (await c.list_tools()).tools)
        check("도구 목록", tools == ["check_citation", "get_document", "list_matters", "search_documents"], tools)
        lm = await call(c, "list_matters", {"limit": 100})
        check("list_matters: u_kim 접근 범위(G-A·G-B) 사건", lm["count"] >= 24, lm["count"])
        check("사건번호·당사자는 기본으로 가려진다", all(m["case_no"] == "[사건번호]" and m["plaintiff"] == "[원고]" for m in lm["matters"]))
        res = await call(c, "search_documents", {"query": "채무자의 문자메시지 답장을 채무 승인으로 주장하는 시효중단에 대응한 준비서면", "limit": 5})
        check("search_documents 결과", res["count"] > 0 and all(r["case_no"] == "[사건번호]" for r in res["results"]), [r["doc_id"] for r in res["results"]])
        doc_id = res["results"][0]["doc_id"]
        doc = await call(c, "get_document", {"doc_id": doc_id})
        check("get_document: 마스킹된 본문과 고지", doc["masked"] is True and "residual_risk" in doc and "notice" in doc)
        # 신원 위조 시도: 헤더로 다른 사용자를 주장해도 토큰의 주인(u_kim)으로만 동작
        async with mcp_client(kim["token"], {"X-User-Id": "u_admin", "X-Lawca-User": "u_park", "LAWCA_USER_ID": "u_admin"}) as forged:
            lm2 = await call(forged, "list_matters", {"limit": 100})
            check("신원 위조 헤더는 무시된다(여전히 u_kim의 범위)", lm2["count"] == lm["count"], f"{lm2['count']} == {lm['count']}")

    # --- 격리: u_park 토큰
    g_a_doc = o.execute("select doc_id from documents where access_group='G-A' limit 1").fetchone()[0]
    async with mcp_client(park["token"]) as c:
        lm = await call(c, "list_matters", {"limit": 100})
        check("u_park는 G-C 사건만(8건)", lm["count"] == 8, lm["count"])
        res = await call(c, "search_documents", {"query": "시효중단 문자메시지 승인", "limit": 20})
        groups = {r[0] for r in o.execute("select access_group from documents where doc_id = any(%s)", ([x["doc_id"] for x in res["results"]],))} if res["results"] else set()
        check("다른 그룹 문서가 검색 결과에 없다", groups <= {"G-C"}, groups)
        d = await call(c, "get_document", {"doc_id": g_a_doc})
        check("다른 그룹 문서는 조회 거부(존재 비노출)", "error" in d and "text" not in d, d.get("error"))

    # --- 감사 로그
    rows = o.execute("select tool, args->>'via', (args->>'token_id')::bigint from audit_log where user_id='u_kim' and tool not like 'api:%%' and (args->>'token_id')::bigint = %s order by id", (kim["token_id"],)).fetchall()
    check("감사 로그에 이 토큰의 ID와 접속 방식이 남는다", {"list_matters", "search_documents", "get_document"} <= {r[0] for r in rows} and all(r[1] == "mcp-http" for r in rows), [r[0] for r in rows])
    check("마지막 사용 시각이 기록된다", o.execute("select last_used_at from api_tokens where token_id=%s", (kim["token_id"],)).fetchone()[0] is not None)

    # --- 폐기·만료·비활성
    victim, vweb, vh = new_token("u_lee", "폐기 시험")
    async with mcp_client(victim.json()["token"]) as c:
        check("(폐기 전) u_lee 토큰 동작", (await call(c, "list_matters", {}))["count"] >= 1)
    vweb.delete(f"/api/tokens/{victim.json()['token_id']}", headers=vh)
    check("폐기 즉시 401", raw_post(victim.json()["token"]).status_code == 401)

    exp = new_token("u_lee", "만료 시험")[0].json()
    o.execute("update api_tokens set expires_at = now() - interval '1 second' where token_id=%s", (exp["token_id"],))
    check("만료된 토큰 401", raw_post(exp["token"]).status_code == 401)

    live = new_token("u_lee", "비활성 시험")[0].json()
    check("(비활성 전) 200", raw_post(live["token"]).status_code == 200)
    o.execute("update users set active=false where user_id='u_lee'")
    try:
        check("계정 비활성화 즉시 401", raw_post(live["token"]).status_code == 401)
    finally:
        o.execute("update users set active=true where user_id='u_lee'")

    # --- 호출 속도 제한(사용자당 분당 120회)
    # 한도는 '60초 안의 횟수'이므로 순서대로 보내면(요청당 수백 ms) 넘지 않는다 → 동시에 몰아서 보낸다.
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(32) as pool:
        codes = list(pool.map(lambda _: raw_post(park["token"]).status_code, range(160)))
    check("분당 호출 한도(120) 초과 시 429", codes.count(200) <= 120 and codes.count(429) >= 30 and set(codes) <= {200, 429},
          f"200={codes.count(200)} 429={codes.count(429)}")
    check("한도는 사용자별이다(다른 사용자는 영향 없음)", raw_post(kim["token"]).status_code == 200)

    # --- 정리
    for tok, web, h in ((kim, kim_web, kim_h), (park, park_web, park_h)):
        web.delete(f"/api/tokens/{tok['token_id']}", headers=h)
    o.execute("update api_tokens set revoked_at = now() where name in ('http-e2e','폐기 시험','만료 시험','비활성 시험') and revoked_at is null")
    print("\n결과:", f"{sum(results)}/{len(results)} 통과")


asyncio.run(main())
sys.exit(0 if all(results) else 1)
