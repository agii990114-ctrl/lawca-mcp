import sys, re, math, time, json, numpy as np
from collections import Counter, defaultdict
from kiwipiepy import Kiwi

data = json.load(open("big/data.json", encoding="utf-8"))
DOCS, QS = data["docs"], data["queries"]
ids = [x["id"] for x in DOCS]
kiwi = Kiwi()
clean = {x["id"]: "\n".join(x["lines"]) for x in DOCS}
ocr = {i: open(f"big/ocr/{i}.md", encoding="utf-8").read() for i in ids}
kiw = {i: "\n".join(kiwi.space(l) if l.strip() else l for l in ocr[i].split("\n")) for i in ids}
variants = {"정답 텍스트": clean, "OCR 원본": ocr, "OCR+kiwi": kiw}
queries = [q["q"] for q in QS]
KINDS = ["주제", "사건번호", "당사자", "금액"]


def ndcg10(ranked, rel):
    dcg = sum(1 / math.log2(r + 2) for r, d in enumerate(ranked[:10]) if d in rel)
    ideal = sum(1 / math.log2(r + 2) for r in range(min(len(rel), 10)))
    return dcg / ideal


def evaluate(S):
    """S: [nq, nd] 점수. 유형별 (hit@1, P@5, nDCG@10, MRR)"""
    acc = defaultdict(list)
    for qi, q in enumerate(QS):
        ranked = [ids[j] for j in np.argsort(-S[qi])]
        rel = set(q["rel"])
        first = next(r for r, d in enumerate(ranked) if d in rel) + 1
        acc[q["kind"]].append((
            ranked[0] in rel,
            sum(d in rel for d in ranked[:5]) / min(5, len(rel)),
            ndcg10(ranked, rel),
            1 / first,
        ))
    return {k: np.mean(v, axis=0) for k, v in acc.items()}


def grams(t, ns=(2, 3)):
    t = re.sub(r"\s+", "", t)
    c = Counter()
    for n in ns:
        c.update(t[i:i + n] for i in range(len(t) - n + 1))
    return c


def bm25(docs, k1=1.2, b=0.75):
    dg = [grams(docs[i]) for i in ids]
    N = len(dg)
    lens = [sum(g.values()) for g in dg]
    avg = sum(lens) / N
    df = Counter()
    for g in dg:
        df.update(g.keys())
    S = np.zeros((len(queries), N))
    for qi, q in enumerate(queries):
        for g in grams(q):
            if g not in df:
                continue
            idf = math.log(1 + (N - df[g] + .5) / (df[g] + .5))
            for j, dgc in enumerate(dg):
                tf = dgc.get(g, 0)
                if tf:
                    S[qi, j] += idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * lens[j] / avg))
    return S


def rrf(*Ss, k=60):
    out = np.zeros_like(Ss[0])
    for S in Ss:
        order = np.argsort(-S, axis=1)
        for qi in range(S.shape[0]):
            out[qi, order[qi]] += 1 / (k + np.arange(S.shape[1]) + 1)
    return out


def show(tag, by_variant):
    print(f"\n### {tag}")
    print(f"{'':12s}" + "".join(f"| {k}(hit@1 P@5 nDCG10) " for k in KINDS[:1]) + "".join(f"| {k}(hit@1 MRR) " for k in KINDS[1:]))
    for v, S in by_variant.items():
        r = evaluate(S)
        a = r["주제"]
        row = f"{v:12s}| {a[0]:.2f} {a[1]:.2f} {a[2]:.2f}       "
        for k in KINDS[1:]:
            row += f"| {r[k][0]:.2f} {r[k][3]:.2f}      "
        print(row, flush=True)


lex = {v: bm25(d) for v, d in variants.items()}
show("문자 2-3gram 키워드(BM25)", lex)

for model in sys.argv[1:]:
    from fastembed import TextEmbedding
    t = time.time()
    m = TextEmbedding(model_name=model)
    Q = np.array(list(m.query_embed(queries)))
    Qn = Q / np.linalg.norm(Q, axis=1, keepdims=True)
    dense = {}
    for v, d in variants.items():
        t1 = time.time()
        D = np.array(list(m.passage_embed([d[i] for i in ids])))
        Dn = D / np.linalg.norm(D, axis=1, keepdims=True)
        dense[v] = Qn @ Dn.T
        print(f"  [{model.split('/')[-1]}] {v} 임베딩 {time.time()-t1:.0f}s", flush=True)
    show(f"임베딩 {model}", dense)
    show(f"하이브리드(RRF) 임베딩+키워드 — {model}", {v: rrf(dense[v], lex[v]) for v in variants})
