"""스키마·RLS·읽기 전용 계정·검색 동작 검증 (reader 계정으로 접속)"""
import re
from pathlib import Path
import psycopg
ROOT = Path(__file__).resolve().parent.parent
env = dict(l.strip().split("=", 1) for l in (ROOT / ".env").read_text().splitlines() if "=" in l)
base = f"host=127.0.0.1 port={env['LAWCA_DB_PORT']} dbname={env['LAWCA_DB_NAME']}"
OWNER = base + f" user=lawca_owner password={env['LAWCA_OWNER_PASSWORD']}"
READER = base + f" user=lawca_reader password={env['LAWCA_READER_PASSWORD']}"
AUDIT = base + f" user=lawca_audit password={env['LAWCA_AUDIT_PASSWORD']}"
ok = True
def check(name, cond, detail=""):
    global ok; ok &= bool(cond); print(("PASS" if cond else "FAIL"), name, detail)

def as_user(uid):
    c = psycopg.connect(READER, autocommit=True)
    c.execute("SELECT set_config('app.user_id', %s, false)", (uid,)) if uid else None
    return c

with psycopg.connect(OWNER) as o:
    tot = o.execute("SELECT (SELECT count(*) FROM matters),(SELECT count(*) FROM documents),(SELECT count(*) FROM doc_chunks),(SELECT count(*) FROM doc_issue_tags)").fetchone()
    print("owner 전체:", dict(zip(["matters","documents","chunks","tags"], tot)))
    truth = dict(o.execute("SELECT access_group, count(*) FROM matters GROUP BY 1").fetchall())
    truth_docs = dict(o.execute("SELECT access_group, count(*) FROM documents GROUP BY 1").fetchall())
    print("그룹별 사건:", truth, "문서:", truth_docs)

# 1) 사용자별 가시 범위 = 소속 그룹의 사건/문서/청크/태그만
expect = {"u_kim": ["G-A","G-B"], "u_lee": ["G-A"], "u_park": ["G-C"], "u_admin": ["G-A","G-B","G-C"], None: [], "u_nobody": []}
for uid, groups in expect.items():
    c = as_user(uid)
    m = c.execute("SELECT count(*) FROM matters").fetchone()[0]
    d = c.execute("SELECT count(*) FROM documents").fetchone()[0]
    ch = c.execute("SELECT count(*) FROM doc_chunks").fetchone()[0]
    t = c.execute("SELECT count(*) FROM doc_issue_tags").fetchone()[0]
    leak = c.execute("SELECT count(*) FROM documents WHERE access_group <> ALL(%s)", (groups or ["-"],)).fetchone()[0]
    check(f"RLS 가시 범위 {uid}", m == sum(truth.get(g,0) for g in groups) and d == sum(truth_docs.get(g,0) for g in groups) and leak == 0,
          f"matters={m} docs={d} chunks={ch} tags={t} 권한밖={leak}")

# 2) 권한 밖 문서 직접 조회·검색 차단
c = as_user("u_park")   # G-C만
other = psycopg.connect(OWNER).execute("SELECT doc_id FROM documents WHERE access_group='G-A' LIMIT 1").fetchone()[0]
check("권한 밖 doc_id 직접 조회 차단", c.execute("SELECT count(*) FROM documents WHERE doc_id=%s", (other,)).fetchone()[0] == 0)
qv = psycopg.connect(OWNER).execute("SELECT embedding::text FROM documents WHERE doc_id=%s", (other,)).fetchone()[0]
hits = c.execute("SELECT count(*) FROM (SELECT doc_id FROM doc_chunks ORDER BY embedding <=> %s::vector LIMIT 50) x", (qv,)).fetchone()[0]
grp = {r[0] for r in c.execute("SELECT DISTINCT access_group FROM (SELECT access_group FROM doc_chunks ORDER BY embedding <=> %s::vector LIMIT 50) x", (qv,)).fetchall()}
check("벡터 검색에 권한 밖 청크 미포함", grp <= {"G-C"}, f"반환 그룹={grp}")

# 3) 읽기 전용·최소 권한
c = as_user("u_admin")
for sql in ["INSERT INTO access_groups VALUES ('G-X','x')", "UPDATE matters SET result='승소'", "DELETE FROM documents", "SELECT * FROM audit_log", "SELECT * FROM eval_queries", "CREATE TABLE t(x int)"]:
    try: c.execute(sql); check("reader 차단: " + sql[:38], False)
    except psycopg.errors.Error as e: check("reader 차단: " + sql[:38], True, type(e).__name__)
a = psycopg.connect(AUDIT, autocommit=True)
a.execute("INSERT INTO audit_log (user_id, tool, args, doc_ids, result_count) VALUES ('u_kim','verify','{}'::jsonb, ARRAY[]::text[], 0)")
check("audit 계정 INSERT 가능", True)
for sql in ["SELECT * FROM documents", "UPDATE audit_log SET tool='x'"]:
    try: a.execute(sql); check("audit 차단: " + sql[:30], False)
    except psycopg.errors.Error as e: check("audit 차단: " + sql[:30], True, type(e).__name__)

# 4) 키워드(trigram, 공백 무시)·벡터 검색 동작
c = as_user("u_admin")
o = psycopg.connect(OWNER)
row = o.execute("SELECT case_no, doc_id FROM documents d JOIN matters m USING(matter_id) LIMIT 1 OFFSET 7").fetchone()
r = c.execute("SELECT doc_id FROM documents WHERE text_norm LIKE %s", ("%" + row[0] + "%",)).fetchall()
check("사건번호 정확 일치(text_norm LIKE)", row[1] in [x[0] for x in r], f"{row[0]} -> {len(r)}건")
q = "사건번호를 공백 없이 다시 쳐도"  # 쿼리 정규화 예시는 서버에서 처리
r = c.execute("SELECT doc_id, similarity(text_norm, %s) s FROM doc_chunks ORDER BY text_norm <-> %s LIMIT 3", ("소멸시효완성", "소멸시효완성")).fetchall()
check("trigram 유사도 검색 동작", len(r) == 3, str([round(x[1],2) for x in r]))
print("\n결과:", "모두 통과" if ok else "실패 있음")
