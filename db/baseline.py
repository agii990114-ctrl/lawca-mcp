"""준비서면 더미 세트 검색 기준선: DB(pgvector)에서 문서 임베딩/청크 임베딩 검색 (owner 계정, 권한 필터 없음)"""
from pathlib import Path
import psycopg, numpy as np, collections
from fastembed import TextEmbedding
ROOT = Path(__file__).resolve().parent.parent
env = dict(l.strip().split("=", 1) for l in (ROOT / ".env").read_text().splitlines() if "=" in l)
dsn = f"host=127.0.0.1 port={env['LAWCA_DB_PORT']} dbname={env['LAWCA_DB_NAME']} user=lawca_owner password={env['LAWCA_OWNER_PASSWORD']}"
m = TextEmbedding(model_name="intfloat/multilingual-e5-large")
with psycopg.connect(dsn) as c:
    qs = c.execute("SELECT query, qtype, gold_doc_id, coalesce(gold_docs, ARRAY[gold_doc_id]) FROM eval_queries WHERE qtype IN ('counter','detail')").fetchall()
    tags = collections.defaultdict(set)
    for d, t in c.execute("SELECT doc_id, tag FROM doc_issue_tags"): tags[d].add(t)
    briefs = {r[0] for r in c.execute("SELECT doc_id FROM documents WHERE kind='준 비 서 면'")}
    Q = list(m.query_embed([q[0] for q in qs]))
    res = collections.defaultdict(list)
    for (q, qt, gold, _), v in zip(qs, Q):
        vs = "[" + ",".join(f"{x:.6f}" for x in v) + "]"
        for name, sql in [("문서 임베딩", "SELECT doc_id FROM documents ORDER BY embedding <=> %s::vector LIMIT 10"),
                          ("청크 임베딩(문서별 최고)", "SELECT doc_id FROM (SELECT doc_id, min(embedding <=> %s::vector) d FROM doc_chunks GROUP BY doc_id) x ORDER BY d LIMIT 10")]:
            ranked = [r[0] for r in c.execute(sql, (vs,)).fetchall()]
            pos = ranked.index(gold) + 1 if gold in ranked else 99
            top1_same_tag = bool(ranked) and ranked[0] in briefs and bool(tags[ranked[0]] & tags[gold])
            res[(name, qt)].append((pos == 1, pos <= 3, pos <= 10, 1 / pos if pos < 99 else 0, top1_same_tag))
print(f"{'방식':22s}{'질의':8s}{'n':>4s} hit@1 hit@3 hit@10  MRR  | 1위가 같은 쟁점의 준비서면")
for (name, qt), v in sorted(res.items()):
    a = np.mean(v, axis=0)
    print(f"{name:22s}{qt:8s}{len(v):4d}  {a[0]:.2f}  {a[1]:.2f}  {a[2]:.2f}   {a[3]:.2f} |   {a[4]:.2f}")
