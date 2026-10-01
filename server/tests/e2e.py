"""MCP 서버 종단 테스트: 실제 stdio 클라이언트로 서버를 띄워 도구를 호출한다."""
import asyncio, json, os, re, sys
from pathlib import Path
import psycopg
from mcp.client.client import Client
from mcp.client.stdio import StdioServerParameters

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "server"))
import db  # noqa: E402
PY = str(ROOT / "server" / ".venv" / "Scripts" / "python.exe")
SERVER = str(ROOT / "server" / "lawca_server.py")
OWNER = db._BASE + f" user=lawca_owner password={db.ENV['LAWCA_OWNER_PASSWORD']}"
results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name, detail, flush=True)


def payload(res):
    if getattr(res, "structured_content", None):
        return res.structured_content
    return json.loads(res.content[0].text)


def client_for(uid):
    env = dict(os.environ, PYTHONUTF8="1")
    env.pop("LAWCA_USER_ID", None)
    if uid:
        env["LAWCA_USER_ID"] = uid
    return Client(StdioServerParameters(command=PY, args=[SERVER], env=env, cwd=str(ROOT)), read_timeout_seconds=180)


async def call(c, tool, args=None):
    r = await c.call_tool(tool, args or {})
    return r, (payload(r) if not getattr(r, "is_error", False) else None)


async def main():
    o = psycopg.connect(OWNER, autocommit=True)
    grp_of_matter = dict(o.execute("SELECT matter_id, access_group FROM matters").fetchall())
    grp_of_doc = dict(o.execute("SELECT doc_id, access_group FROM documents").fetchall())
    o.execute("TRUNCATE audit_log")

    # 1) 도구 목록
    async with client_for("u_admin") as c:
        tools = await c.list_tools()
        names = sorted(t.name for t in tools.tools)
        check("도구 목록", names == ["check_citation", "get_document", "list_matters", "search_documents"], names)
        desc = {t.name: t.description for t in tools.tools}
        check("도구 설명에 식별자 전용 인자·OCR 한계 안내 포함",
              "정확 일치" in desc["search_documents"] and "OCR" in desc["search_documents"])

    # 1-1) check_citation: 등록된 조문은 통과, 기억에 의존한 판례·미등록 조문은 인용 불가
    async with client_for("u_lee") as c:
        text = ("민법 제168조 제3호에 따라 승인은 시효중단 사유이다. 대법원 2005. 2. 17. 선고 2004다59959 판결. "
                "민법 제174조도 본다. 대법원 2099. 1. 1. 선고 2001다3672 판결.")
        _, p = await call(c, "check_citation", {"text": text})
        byk = {r["citation"]: r for r in (p or {}).get("results", [])}
        check("check_citation: 등록 조문(민법 제168조)은 ok_to_cite", byk.get("민법 제168조", {}).get("ok_to_cite") is True, byk.get("민법 제168조"))
        check("check_citation: 미등록 조문(제174조)은 인용 불가", byk.get("제174조", {}).get("ok_to_cite") is False or byk.get("민법 제174조", {}).get("ok_to_cite") is False)
        check("check_citation: 등록·외부 확인 없는 판례는 인용 불가(unverified)",
              byk.get("2004다59959", {}).get("status") == "unverified" and byk["2004다59959"]["ok_to_cite"] is False, byk.get("2004다59959"))
        check("check_citation: 미래 선고일은 inconsistent", byk.get("2001다3672", {}).get("status") == "inconsistent", byk.get("2001다3672"))
        check("check_citation: 판례는 요지 미확인을 명시", byk.get("2004다59959", {}).get("holding_checked") is False)
        check("check_citation: 인용 불가 항목에 대한 조치 안내", "action" in (p or {}))
        o.execute("INSERT INTO verified_citations (kind,key,label,summary,decision_date,source,verified_by)"
                  " VALUES ('case','2004다59959','물품대금','채무승인의 방식','2005-02-17','e2e 시험','e2e')"
                  " ON CONFLICT DO NOTHING")
        _, p2 = await call(c, "check_citation", {"text": "대법원 2005. 2. 17. 선고 2004다59959 판결"})
        r0 = (p2 or {}).get("results", [{}])[0]
        check("check_citation: 등록부에 넣으면 verified_registry", r0.get("status") == "verified_registry" and r0.get("ok_to_cite") is True, r0)
        _, p3 = await call(c, "check_citation", {"text": "대법원 2005. 2. 18. 선고 2004다59959 판결"})
        check("check_citation: 선고일이 다르면 date_mismatch", (p3 or {}).get("results", [{}])[0].get("status") == "date_mismatch")
        o.execute("DELETE FROM verified_citations WHERE key = '2004다59959' AND verified_by = 'e2e'")
        n_audit = o.execute("SELECT count(*) FROM audit_log WHERE tool='check_citation'").fetchone()[0]
        body = o.execute("SELECT args::text FROM audit_log WHERE tool='check_citation' LIMIT 1").fetchone()[0]
        check("check_citation: 감사 로그에는 건수만 남고 본문 인용은 남지 않음", n_audit >= 3 and "2004다59959" not in body, body)

    # 1-2) 긴 서면(약 20쪽): 기본 응답은 한 부분만, section·part로 나눠 읽기, 마스킹 유지
    sys.path.insert(0, str(ROOT))
    from backend.app.ingest import chunking
    a_matter = next(m for m, g in grp_of_matter.items() if g == "G-A")
    base = [r[0] for r in o.execute("SELECT full_text FROM documents WHERE kind LIKE '준%' ORDER BY doc_id LIMIT 30")]
    body = "\n\n".join(f"{'가나다라마바사아자차카타파하'[i % 14]}. 쟁점 {i + 1}\n" + " ".join(t.split()) for i, t in enumerate(base * 2))
    long_text = ("준 비 서 면\n\n1. 상대방 주장의 요지\n원고는 연락처 010-1234-5678 로 문의하였다고 주장합니다. "
                 "목격자 박다람이 이를 보았다고 하며 서울 마포구 성산로 45, 302호에서 만났다고 합니다.\n\n"
                 "2. 인정 및 부인\n가. 대여 사실은 인정합니다.\n\n3. 쟁점별 반박\n" + body +
                 "\n\n4. 입증방법\n을 제1호증 문자메시지\n\n5. 결론\n원고의 청구는 기각되어야 합니다.")
    LONG_ID = "zz_long_e2e"
    o.execute("DELETE FROM documents WHERE doc_id = %s", (LONG_ID,))
    o.execute("INSERT INTO documents (doc_id, matter_id, kind, round, author_side, version, full_text, text_norm, access_group)"
              " VALUES (%s,%s,'준 비 서 면',1,'피고','최종본',%s,%s,'G-A')", (LONG_ID, a_matter, long_text, long_text.replace(" ", "")))
    for i, (sec, txt) in enumerate(chunking.chunk_text(long_text)):
        o.execute("INSERT INTO doc_chunks (doc_id, seq, section, text, text_norm, access_group) VALUES (%s,%s,%s,%s,%s,'G-A')",
                  (LONG_ID, i, sec, txt, txt.replace(" ", "")))
    try:
        async with client_for("u_lee") as c:
            _, d1 = await call(c, "get_document", {"doc_id": LONG_ID})
            check(f"긴 서면({len(long_text):,}자): 기본 응답은 한 부분(8,000자 이내)만",
                  d1 and d1["total_parts"] > 1 and len(d1["text"]) <= 8000 and d1["part"] == 1 and "next_part_hint" in d1,
                  (d1 or {}).get("total_parts"))
            check("긴 서면: 전체 글자 수와 섹션 목록을 알려 준다", d1["total_chars"] > 8000 and len(d1["sections"]) >= 5)
            _, d2 = await call(c, "get_document", {"doc_id": LONG_ID, "part": 2})
            check("긴 서면: part=2 는 다음 부분", d2["part"] == 2 and d2["text"] != d1["text"])
            _, dl = await call(c, "get_document", {"doc_id": LONG_ID, "part": d1["total_parts"]})
            check("긴 서면: 마지막 부분까지 읽을 수 있다", dl["part"] == d1["total_parts"] and "기각" in dl["text"])
            _, d3 = await call(c, "get_document", {"doc_id": LONG_ID, "part": 99})
            check("긴 서면: 범위 밖 part 는 오류", d3 and "error" in d3)
            _, ds = await call(c, "get_document", {"doc_id": LONG_ID, "section": "입증방법"})
            check("section='입증방법': 그 섹션만 반환", ds["total_parts"] == 1 and "을 제1호증" in ds["text"] and "쟁점 1" not in ds["text"], ds["text"][:60])
            _, ds2 = await call(c, "get_document", {"doc_id": LONG_ID, "section": "쟁점별 반박 > 나"})
            check("section='쟁점별 반박 > 나': 해당 소절만", ds2 and "error" not in ds2 and ds2["total_chars"] < d1["total_chars"] / 2, (ds2 or {}).get("total_chars"))
            _, dn = await call(c, "get_document", {"doc_id": LONG_ID, "section": "없는섹션"})
            check("없는 section: 사용 가능한 목록을 돌려준다", dn and "error" in dn and len(dn.get("sections", [])) >= 5)
            _, dm = await call(c, "get_document", {"doc_id": LONG_ID, "section": "상대방 주장"})
            check("분할·섹션 조회에도 마스킹 유지(전화번호)", "010-1234-5678" not in dm["text"] and "[전화번호]" in dm["text"], dm["text"][:80])
            check("주소가 마스킹된다", "성산로" not in dm["text"] and "[주소]" in dm["text"], dm["text"][:160])
            check("등록 전에는 이름만 있는 제3자(박다람)가 남는다(규칙은 역할어·호칭이 있을 때만 작동)", "박다람" in dm["text"])
            o.execute("UPDATE matters SET related_names = ARRAY['박다람'] WHERE matter_id = %s", (a_matter,))
            _, dm2 = await call(c, "get_document", {"doc_id": LONG_ID, "section": "상대방 주장"})
            check("사건에 관련자로 등록하면 그 이름이 마스킹된다", "박다람" not in dm2["text"] and "[관련자]" in dm2["text"], dm2["text"][:160])
            _, dsh = await call(c, "get_document", {"doc_id": "d6101b"})
            check("짧은 서면은 종전과 같이 한 번에 전체 반환", dsh["total_parts"] == 1 and "next_part_hint" not in dsh and dsh["part"] == 1)
    finally:
        o.execute("UPDATE matters SET related_names = '{}' WHERE matter_id = %s", (a_matter,))
        o.execute("DELETE FROM documents WHERE doc_id = %s", (LONG_ID,))

    # 2) 사용자별 list_matters
    for uid, groups in {"u_park": {"G-C"}, "u_lee": {"G-A"}, "u_kim": {"G-A", "G-B"},
                        "u_admin": {"G-A", "G-B", "G-C"}}.items():
        async with client_for(uid) as c:
            _, p = await call(c, "list_matters", {"limit": 100})
            gs = {grp_of_matter[m["matter_id"]] for m in p["matters"]}
            exp = sum(1 for g in grp_of_matter.values() if g in groups)
            check(f"list_matters 권한 범위 {uid}", p["count"] == exp and gs <= groups, f"count={p['count']} groups={sorted(gs)}")

    # 3) fail closed
    async with client_for(None) as c:
        r, p = await call(c, "list_matters")
        check("사용자 미지정 시 거부", getattr(r, "is_error", False) or (p and "error" in p), str(r)[:100])
    async with client_for("u_ghost") as c:
        r, p = await call(c, "list_matters")
        check("미등록 사용자 거부", getattr(r, "is_error", False), str(r)[:100])

    # 4) 검색 품질 (평가 질의, u_admin) + 식별자
    qs = o.execute("SELECT query, qtype, gold_doc_id, coalesce(gold_docs, ARRAY[gold_doc_id]) FROM eval_queries").fetchall()
    stats = {}
    async with client_for("u_admin") as c:
        for q, qt, gold, golds in qs:
            args = {"query": q, "limit": 10}
            if qt == "case_no":
                args = {"case_no": q, "limit": 10}
            if qt == "party":
                args = {"party_name": q.replace("원고 ", ""), "limit": 10}
            if qt in ("case_no", "party"):    # 웹으로 문서가 더 올라와도 성립하도록, 정답은 '그 사건의 모든 문서'
                golds = [r[0] for r in o.execute("select doc_id from documents where matter_id = (select matter_id from documents where doc_id = %s)", (gold,))]
                args["limit"] = 20
            _, p = await call(c, "search_documents", args)
            ids = [r["doc_id"] for r in p["results"]]
            pos = min([ids.index(g) + 1 for g in golds if g in ids] or [99])
            stats.setdefault(qt, []).append(pos)
        for qt, v in stats.items():
            h1 = sum(x == 1 for x in v) / len(v)
            h3 = sum(x <= 3 for x in v) / len(v)
            h10 = sum(x <= 10 for x in v) / len(v)
            print(f"   검색 {qt:8s} n={len(v):3d} hit@1={h1:.2f} hit@3={h3:.2f} hit@10={h10:.2f}")
        check("식별자 필터(case_no/party) 정확도 100%", all(x <= 2 for x in stats["case_no"] + stats["party"]))
        topical = stats["counter"] + stats["detail"]
        check("주제 질의 hit@3 >= 0.9", sum(x <= 3 for x in topical) / len(topical) >= 0.9,
              f"{sum(x <= 3 for x in topical) / len(topical):.2f}")

        m = o.execute("SELECT m.case_no, m.matter_id FROM matters m LIMIT 1 OFFSET 5").fetchone()
        _, p = await call(c, "search_documents", {"query": f"{m[0]} 사건 서면 찾아줘"})
        check("질의문 사건번호 자동 승격", p["results"] and all(r["matter_id"] == m[1] for r in p["results"]) and p.get("notes"),
              str(p.get("notes")))
        amt = o.execute(r"SELECT (regexp_matches(full_text, '(\d{1,3}(?:,\d{3})+)원'))[1], doc_id FROM documents LIMIT 1 OFFSET 3").fetchone()
        _, p = await call(c, "search_documents", {"amount": amt[0]})
        check("금액 정확 일치 필터", amt[1] in [r["doc_id"] for r in p["results"]], f"{amt[0]} -> {[r['doc_id'] for r in p['results']]}")
        r, p = await call(c, "search_documents", {})
        check("인자 없는 검색은 오류", getattr(r, "is_error", False) or (p and "error" in str(p)))
        _, p = await call(c, "search_documents", {"case_no": "1999가합99999"})
        check("없는 식별자는 근거 없음", p["count"] == 0 and "근거 없음" in p.get("note_empty", ""))

    # 4-2) 식별정보 기본 비노출: list_matters / search_documents 결과에 DB의 사건번호·당사자·담당 변호사가 없어야 한다
    ident = set()
    for cn, pl, df, at in o.execute("SELECT case_no, plaintiff, defendant, attorney_name FROM matters"):
        ident |= {cn, pl, df, re.sub(r"^변호사\s*", "", at or "")}
    ident.discard("")
    async with client_for("u_admin") as c:
        _, lm = await call(c, "list_matters", {"limit": 100})
        blob = json.dumps(lm["matters"], ensure_ascii=False)
        found = sorted(s for s in ident if s in blob)
        check("list_matters 기본값은 식별정보 비노출", lm["count"] == 32 and not found, str(found[:4]))
        check("list_matters 자리표시자와 matter_id 유지", all(m["case_no"] == "[사건번호]" and m["plaintiff"] == "[원고]" and m["matter_id"] for m in lm["matters"]))
        # 결과 문서 '자신의 사건' 식별정보(사건번호·당사자·담당 변호사)가 그 결과에 없어야 한다.
        # (다른 사건의 당사자와 이름이 같은 제3자가 본문에 나오는 경우는 알려진 한계라 여기서 판정하지 않는다)
        own = {mid: {cn, pl, df, re.sub(r"^변호사\s*", "", at or "")} - {""}
               for mid, cn, pl, df, at in o.execute("SELECT matter_id, case_no, plaintiff, defendant, attorney_name FROM matters")}
        found_s, n_s = [], 0
        for q, qt, gold, golds in qs:
            if qt in ("case_no", "party"):
                continue
            _, p = await call(c, "search_documents", {"query": q, "limit": 5})
            n_s += 1
            for r in p["results"]:
                blob = json.dumps(r, ensure_ascii=False)
                found_s += [(r["doc_id"], s) for s in own[r["matter_id"]] if s in blob]
        check(f"search_documents 기본값은 결과 자신의 식별정보 비노출(best_section·snippet 포함, {n_s}개 질의)", not found_s, str(found_s[:4]))
        _, lm2 = await call(c, "list_matters", {"limit": 3, "include_identifiers": True})
        check("include_identifiers=true 는 실제 값 반환", all(m["case_no"] in ident and m["plaintiff"] in ident for m in lm2["matters"]))
        _, sp = await call(c, "search_documents", {"query": "소멸시효", "limit": 3, "include_identifiers": True})
        check("search include_identifiers=true 는 사건번호 반환", all(r["case_no"] in ident for r in sp["results"]))
        # 마스킹 상태에서도 식별자 '입력' 필터는 동작해야 한다
        pn = o.execute("SELECT plaintiff, matter_id FROM matters LIMIT 1 OFFSET 2").fetchone()
        _, pf = await call(c, "search_documents", {"party_name": pn[0]})
        check("식별자 필터는 마스킹과 무관하게 동작", pf["results"] and all(r["matter_id"] == pn[1] for r in pf["results"]))

    # 5) 권한 밖 차단 (u_park = G-C 전용)
    ga_doc = next(d for d, g in grp_of_doc.items() if g == "G-A")
    ga_query = o.execute("SELECT query FROM eval_queries WHERE qtype='counter' AND gold_doc_id IN "
                         "(SELECT doc_id FROM documents WHERE access_group='G-A') LIMIT 1").fetchone()[0]
    async with client_for("u_park") as c:
        _, p = await call(c, "search_documents", {"query": ga_query, "limit": 20})
        check("권한 밖 문서가 검색에 미노출", all(grp_of_doc[r["doc_id"]] == "G-C" for r in p["results"]), f"{len(p['results'])}건")
        _, p1 = await call(c, "get_document", {"doc_id": ga_doc})
        _, p2 = await call(c, "get_document", {"doc_id": "d-does-not-exist"})
        check("권한 밖과 없는 문서의 응답이 동일(존재 비노출)", p1.get("error") == p2.get("error") and "text" not in p1, p1.get("error"))

    # 6) 마스킹
    pii = {"주민번호": re.compile(r"\d{6}-[1-8]\d{6}"), "전화": re.compile(r"01\d-\d{3,4}-\d{4}"),
           "이메일": re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "계좌": re.compile(r"\d{2,6}-\d{2,6}-\d{4,8}"),
           "금액": re.compile(r"\d[\d,]*\s*원(?!고|본|금|인|칙|래|장)"),
           # 반쯤 가려진 금액 조각('2억 [금액]'의 앞쪽 '2억')도 누출로 본다. 이전 검사는 완전한 '숫자원'만 찾아 이 결함을 놓쳤다.
           "금액 조각(억·천만…)": re.compile(r"\d\s*(?:억|천만|백만|십만)"), "금액 조각(만 원)": re.compile(r"\d[\d,]*\s*만\s*원")}
    rows = o.execute("SELECT d.doc_id, m.plaintiff, m.defendant, m.case_no FROM documents d JOIN matters m USING(matter_id)").fetchall()
    leaks = []
    async with client_for("u_admin") as c:
        for doc_id, pl, df, cn in rows:
            _, p = await call(c, "get_document", {"doc_id": doc_id})
            t = p["text"]
            for k, rx in pii.items():
                if rx.search(t):
                    leaks.append((doc_id, k, rx.search(t).group(0)))
            for nm in (pl, df, cn):
                if nm in t:
                    leaks.append((doc_id, "이름/사건번호", nm))
                if any(nm in s for s in p["sections"]):
                    leaks.append((doc_id, "섹션명에 식별정보", nm))
        check("64개 문서 마스킹 후 PII·당사자·사건번호·금액 잔존 0건", not leaks, str(leaks[:6]))
        _, raw = await call(c, "get_document", {"doc_id": rows[0][0], "mask": False})
        check("mask=false 는 원문(경고 포함)", rows[0][3] in raw["text"] and "warning" in raw)
        _, mk = await call(c, "get_document", {"doc_id": rows[0][0]})
        check("마스킹본에 주입 방어 안내·잔여 위험 고지", "notice" in mk and "residual_risk" in mk and mk["case_no"] == "[사건번호]")
        _, p = await call(c, "search_documents", {"query": "소멸시효 완성 항변", "limit": 10})
        bad = [r["doc_id"] for r in p["results"] if any(rx.search(r["snippet"]) for rx in pii.values())]
        check("검색 snippet 마스킹", not bad, str(bad))

    # 7) 감사 로그
    a = o.execute("SELECT tool, count(*) FROM audit_log GROUP BY 1 ORDER BY 1").fetchall()
    check("감사 로그 기록", {t for t, _ in a} >= {"list_matters", "search_documents", "get_document"}, dict(a))
    raw_logged = o.execute("SELECT count(*) FROM audit_log WHERE tool='get_document' AND (args->>'mask')='false'").fetchone()[0]
    check("원문 조회(mask=false)가 로그에 구분 기록", raw_logged == 1, raw_logged)
    ident_logged = o.execute("SELECT count(*) FROM audit_log WHERE (args->>'include_identifiers')='true'").fetchone()[0]
    check("식별정보 공개 요청(include_identifiers=true)이 로그에 구분 기록", ident_logged == 2, ident_logged)
    users = {r[0] for r in o.execute("SELECT DISTINCT user_id FROM audit_log")}
    check("로그에 사용자 신원 기록", {"u_park", "u_admin"} <= users, users)
    print("\n결과:", f"{sum(results)}/{len(results)} 통과")


asyncio.run(main())
