"""dummy-data/briefs_all.json 을 PostgreSQL에 적재한다 (사건, 문서, 쟁점 태그, 청크, 임베딩, 평가 질의).

사용: python db/load_dummy.py            (기존 데이터를 지우고 다시 적재)
임베딩은 로컬 모델(multilingual-e5-large, fastembed)로 계산한다. 외부 전송 없음.
"""
import json, re, os, sys, time
from pathlib import Path
import psycopg

ROOT = Path(__file__).resolve().parent.parent
env = dict(l.strip().split("=", 1) for l in (ROOT / ".env").read_text().splitlines() if "=" in l)
DSN = f"host=127.0.0.1 port={env['LAWCA_DB_PORT']} dbname={env['LAWCA_DB_NAME']} user=lawca_owner password={env['LAWCA_OWNER_PASSWORD']}"

cases = json.loads((ROOT / "dummy-data" / "briefs_all.json").read_text(encoding="utf-8"))
ns = lambda s: re.sub(r"\s+", "", s)

USERS = [  # 권한 테스트용 사용자
    ("u_kim",   "김서준", "변호사",   ["G-A", "G-B"]),
    ("u_lee",   "이하윤", "변호사",   ["G-A"]),
    ("u_park",  "박지안", "변호사",   ["G-C"]),
    ("u_admin", "관리자", "관리자",   ["G-A", "G-B", "G-C"]),
]
GROUPS = {"G-A": "일반 사건군 A", "G-B": "일반 사건군 B", "G-C": "격리 사건군 C (이해충돌 방지 테스트용)"}

RE_SECTION = re.compile(r"^([1-5])\.\s")
RE_SUB = re.compile(r"^([가-하])\.\s")


def make_chunks(lines):
    """헤더 → 섹션(1~5) → 3번 섹션은 소항목(가·나·다) 단위로 분할. 번호 체계가 없는 서면은 본문 1청크."""
    body = list(lines)
    first = next((i for i, l in enumerate(body) if RE_SECTION.match(l.strip())), None)
    if first is None:
        return [("본문", "\n".join(l for l in body if l.strip()))]
    chunks = []
    head = "\n".join(l for l in body[:first] if l.strip())
    if head:
        chunks.append(("표제", head))
    section, sub, buf = None, None, []

    def flush():
        text = "\n".join(l for l in buf if l.strip())
        if text:
            name = section if not sub else f"{section} > {sub}"
            chunks.append((name, text))

    for l in body[first:]:
        s = l.strip()
        if RE_SECTION.match(s):
            flush(); buf = [s]; section = s; sub = None
        elif section and section.startswith("3.") and RE_SUB.match(s):
            flush(); buf = [s]; sub = s[:30]
        else:
            buf.append(l)
    flush()
    return chunks


def vec(v):
    return "[" + ",".join(f"{x:.6f}" for x in v) + "]"


def main():
    from fastembed import TextEmbedding
    t0 = time.time()
    model = TextEmbedding(model_name="intfloat/multilingual-e5-large")
    print(f"모델 로드 {time.time()-t0:.0f}s", flush=True)

    docs, chunk_rows = [], []
    for c in cases:
        for d in c["docs"]:
            text = "\n".join(d["lines"])
            docs.append((c, d, text))
            for seq, (sec, ctext) in enumerate(make_chunks(d["lines"])):
                chunk_rows.append((d["doc_id"], c["access_group"], seq, sec, ctext))
    print(f"사건 {len(cases)}, 문서 {len(docs)}, 청크 {len(chunk_rows)} — 임베딩 계산 중", flush=True)

    t0 = time.time()
    doc_vecs = list(model.passage_embed([t for _, _, t in docs]))
    print(f"문서 임베딩 {time.time()-t0:.0f}s", flush=True)
    t0 = time.time()
    chunk_vecs = list(model.passage_embed([r[4] for r in chunk_rows]))
    print(f"청크 임베딩 {time.time()-t0:.0f}s", flush=True)

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("TRUNCATE eval_queries, doc_chunks, doc_issue_tags, documents, matters, user_groups, users, access_groups, audit_log RESTART IDENTITY CASCADE")
        for gid, desc in GROUPS.items():
            cur.execute("INSERT INTO access_groups VALUES (%s,%s)", (gid, desc))
        for uid, name, role, groups in USERS:
            cur.execute("INSERT INTO users VALUES (%s,%s,%s)", (uid, name, role))
            for g in groups:
                cur.execute("INSERT INTO user_groups VALUES (%s,%s)", (uid, g))
        for c in cases:
            cur.execute("INSERT INTO matters VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (c["case_id"], c["case_no"], c["case_type"], c["court"], c["plaintiff"], c["defendant"],
                         c["our_side"], c["attorney"], c["result"], c["access_group"]))
        for (c, d, text), v in zip(docs, doc_vecs):
            cur.execute(
                "INSERT INTO documents (doc_id, matter_id, kind, round, author_side, filed_date, full_text, text_norm, access_group, embedding)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::vector)",
                (d["doc_id"], c["case_id"], d["kind"], d["round"], d["author_side"], d["filed_date"], text, ns(text),
                 c["access_group"], vec(v)))
            for tag in d.get("issue_tags", []):
                cur.execute("INSERT INTO doc_issue_tags VALUES (%s,%s) ON CONFLICT DO NOTHING", (d["doc_id"], tag))
        for (doc_id, grp, seq, sec, ctext), v in zip(chunk_rows, chunk_vecs):
            cur.execute("INSERT INTO doc_chunks (doc_id, seq, section, text, text_norm, access_group, embedding)"
                        " VALUES (%s,%s,%s,%s,%s,%s,%s::vector)",
                        (doc_id, seq, sec, ctext, ns(ctext), grp, vec(v)))
        # 평가 질의: 준비서면의 counter/detail 질의 + 식별자 질의
        n = 0
        for c in cases:
            ours = next(d for d in c["docs"] if "queries" in d)
            for q in ours["queries"]:
                cur.execute("INSERT INTO eval_queries (query, qtype, gold_doc_id) VALUES (%s,%s,%s)", (q["q"], q["type"], ours["doc_id"])); n += 1
            both = [d["doc_id"] for d in c["docs"]]   # 사건번호·당사자 질의는 같은 사건의 두 서면 모두 정답
            cur.execute("INSERT INTO eval_queries (query, qtype, gold_doc_id, gold_docs) VALUES (%s,%s,%s,%s)", (c["case_no"], "case_no", ours["doc_id"], both)); n += 1
            cur.execute("INSERT INTO eval_queries (query, qtype, gold_doc_id, gold_docs) VALUES (%s,%s,%s,%s)", ("원고 " + c["plaintiff"], "party", ours["doc_id"], both)); n += 1
        conn.commit()
    print(f"적재 완료 (평가 질의 {n}건)")


if __name__ == "__main__":
    main()
