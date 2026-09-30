"""템플릿 서면 300건(배경) + 에이전트 작성 서면 30건을 섞은 330건 코퍼스에서 30건 대상 질의 평가"""
import sys, re, os, math, time, json, numpy as np
from collections import Counter, defaultdict

tmpl = json.load(open("big/data.json", encoding="utf-8"))["docs"]
real = json.load(open("real/all.json", encoding="utf-8"))
ids = [x["id"] for x in tmpl] + [x["id"] for x in real]
OCR = {x["id"]: open(f"big/ocr/{x['id']}.md", encoding="utf-8").read() for x in tmpl}
OCR.update({x["id"]: open(f"real/ocr/{x['id']}.md", encoding="utf-8").read() for x in real})
ns = lambda s: re.sub(r"\s+", "", s)

# 질의: 실제 서면 30건 x (issue, detail, 사건번호, 당사자, 금액)
QS = []
for x in real:
    for q in x["queries"]:
        QS.append({"kind": "주제(" + q["type"] + ")", "q": q["q"], "rel": {x["id"]}})
    QS.append({"kind": "사건번호", "q": x["case_no"], "rel": {x["id"]}})
    QS.append({"kind": "당사자", "q": "원고 " + x["plaintiff"], "rel": {x["id"]}})
    QS.append({"kind": "금액", "q": f"{x['case_type']} 사건 금 {x['amount']}원", "rel": {x["id"]}})
queries = [q["q"] for q in QS]
KINDS = ["주제(issue)", "주제(detail)", "사건번호", "당사자", "금액"]


def evaluate(S):
    acc = defaultdict(list)
    for qi, q in enumerate(QS):
        ranked = [ids[j] for j in np.argsort(-S[qi], kind="stable")]
        pos = next(r for r, d in enumerate(ranked) if d in q["rel"]) + 1
        acc[q["kind"]].append((pos == 1, pos <= 3, pos <= 10, 1 / pos))
    return {k: np.mean(v, axis=0) for k, v in acc.items()}


def header(t):
    print(f"\n### {t}")
    print(f"{'방식':30s}" + "".join(f"| {k} hit@1/@3/MRR " for k in KINDS), flush=True)


def show(tag, S):
    r = evaluate(S)
    print(f"{tag:30s}" + "".join(f"|   {r[k][0]:.2f} {r[k][1]:.2f} {r[k][3]:.2f}      " for k in KINDS), flush=True)


def grams(t, n_=(2, 3)):
    t = ns(t)
    c = Counter()
    for n in n_:
        c.update(t[i:i + n] for i in range(len(t) - n + 1))
    return c


def bm25(k1=1.2, b=0.75):
    dg = [grams(OCR[i]) for i in ids]
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


RE_CASE = re.compile(r"\d{4}[가-힣]{1,3}\d{3,6}")
RE_AMT = re.compile(r"\d{1,3}(?:,\d{3})+")
RE_PARTY = re.compile(r"(?:원고|피고)\s*(?:주식회사\s*[가-힣]{2,8}|[가-힣]{2,4})(?![가-힣])")


def extract_ids(q):
    out = [("case", m.group(0)) for m in RE_CASE.finditer(q)]
    out += [("amt", m.group(0).replace(",", "")) for m in RE_AMT.finditer(q)]
    out += [("party", m.group(0)) for m in RE_PARTY.finditer(q)]
    return out


DOC_NS = {i: ns(OCR[i]) for i in ids}
DOC_NS_NOCOMMA = {i: DOC_NS[i].replace(",", "") for i in ids}


def route(base):
    S = base.copy()
    for qi, q in enumerate(queries):
        for kind, val in extract_ids(q):
            for j, i in enumerate(ids):
                if (val in DOC_NS_NOCOMMA[i]) if kind == "amt" else (ns(val) in DOC_NS[i]):
                    S[qi, j] += 1000
    return S


def rerank(model_name, base, topn=20, skip_ids=False):
    from fastembed.rerank.cross_encoder import TextCrossEncoder
    m = TextCrossEncoder(model_name=model_name)
    S = base.copy()
    t = time.time(); n = 0
    for qi, q in enumerate(queries):
        if skip_ids and extract_ids(q):
            continue
        top = np.argsort(-base[qi])[:topn]
        sc = np.array(list(m.rerank(q, [OCR[ids[j]] for j in top])))
        n += len(top)
        for rank, k in enumerate(np.argsort(-sc)):
            S[qi, top[k]] = 10000 - rank
    print(f"   [{model_name.split('/')[-1]}] {n}쌍 {time.time()-t:.0f}s", flush=True)
    return S


if __name__ == "__main__":
    lex = bm25()
    cache = "big/dense_real_e5.npy"
    if os.path.exists(cache):
        dense = np.load(cache)
    else:
        from fastembed import TextEmbedding
        m = TextEmbedding(model_name="intfloat/multilingual-e5-large")
        Q = np.array(list(m.query_embed(queries)))
        D = np.array(list(m.passage_embed([OCR[i] for i in ids])))
        dense = (Q / np.linalg.norm(Q, axis=1, keepdims=True)) @ (D / np.linalg.norm(D, axis=1, keepdims=True)).T
        np.save(cache, dense)
    hyb = rrf(dense, lex)
    print(f"코퍼스 {len(ids)}건(템플릿 {len(tmpl)} + 에이전트 작성 {len(real)}), 질의 {len(queries)}개 (OCR 원본 텍스트)")
    header("라우팅")
    show("키워드 단독", lex)
    show("임베딩(e5-large) 단독", dense)
    show("하이브리드 RRF", hyb)
    show("하이브리드 + 식별자 라우팅", route(hyb))
    header("리랭커(상위 20 재정렬, jina 다국어)")
    for mn in sys.argv[1:]:
        show("하이브리드 + 리랭커", rerank(mn, hyb))
        show("라우팅 + 주제만 리랭커", route(rerank(mn, hyb, skip_ids=True)))
