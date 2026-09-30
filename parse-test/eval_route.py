import sys, re, os, math, time, json, numpy as np
from collections import Counter, defaultdict

data = json.load(open("big/data.json", encoding="utf-8"))
DOCS, QS = data["docs"], data["queries"]
ids = [x["id"] for x in DOCS]
ocr = {i: open(f"big/ocr/{i}.md", encoding="utf-8").read() for i in ids}
queries = [q["q"] for q in QS]
KINDS = ["주제", "사건번호", "당사자", "금액"]
ns = lambda s: re.sub(r"\s+", "", s)


# ---------- 평가 ----------
def ndcg10(ranked, rel):
    dcg = sum(1 / math.log2(r + 2) for r, d in enumerate(ranked[:10]) if d in rel)
    return dcg / sum(1 / math.log2(r + 2) for r in range(min(len(rel), 10)))


def evaluate(S):
    acc = defaultdict(list)
    for qi, q in enumerate(QS):
        ranked = [ids[j] for j in np.argsort(-S[qi], kind="stable")]
        rel = set(q["rel"])
        first = next(r for r, d in enumerate(ranked) if d in rel) + 1
        acc[q["kind"]].append((ranked[0] in rel, sum(d in rel for d in ranked[:5]) / min(5, len(rel)),
                               ndcg10(ranked, rel), 1 / first))
    return {k: np.mean(v, axis=0) for k, v in acc.items()}


def show(tag, S):
    r = evaluate(S)
    a = r["주제"]
    row = f"{tag:34s}| {a[0]:.2f} {a[2]:.2f}  "
    for k in KINDS[1:]:
        row += f"| {r[k][0]:.2f} {r[k][3]:.2f}  "
    print(row, flush=True)


def header(title):
    print(f"\n### {title}")
    print(f"{'방식':34s}| 주제 hit@1 nDCG10 " + "".join(f"| {k} hit@1 MRR " for k in KINDS[1:]), flush=True)


# ---------- 키워드(BM25, 문자 2-3gram) ----------
def grams(t, n_=(2, 3)):
    t = ns(t)
    c = Counter()
    for n in n_:
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


# ---------- 질의 라우팅: 식별자 추출 + 정확 일치 ----------
RE_CASE = re.compile(r"\d{4}[가-힣]{1,3}\d{3,6}")
RE_AMT = re.compile(r"\d{1,3}(?:,\d{3})+")
RE_PARTY = re.compile(r"(?:원고|피고)\s*([가-힣]{2,4})(?![가-힣])")


def extract_ids(q):
    out = [("case", m.group(0)) for m in RE_CASE.finditer(q)]
    out += [("amt", m.group(0).replace(",", "")) for m in RE_AMT.finditer(q)]
    out += [("party", m.group(0)) for m in RE_PARTY.finditer(q)]
    return out


def edit1(a, b):  # 편집거리 <= 1 (숫자열 비교용)
    if a == b:
        return True
    if abs(len(a) - len(b)) > 1:
        return False
    i = j = 0
    diff = 0
    while i < len(a) and j < len(b):
        if a[i] == b[j]:
            i += 1; j += 1
            continue
        diff += 1
        if diff > 1:
            return False
        if len(a) > len(b): i += 1
        elif len(a) < len(b): j += 1
        else: i += 1; j += 1
    return diff + (len(a) - i) + (len(b) - j) <= 1


DOC_NS = {i: ns(ocr[i]) for i in ids}
DOC_NUMS = {i: [n.replace(",", "") for n in re.findall(r"\d[\d,]{5,}", ocr[i])] for i in ids}


def id_matches(q, fuzzy_amt):
    """질의의 식별자와 정확히(금액은 옵션으로 1글자 오차 허용) 일치하는 문서 -> 일치 개수"""
    cand = extract_ids(q)
    hit = np.zeros(len(ids))
    for kind, val in cand:
        for j, i in enumerate(ids):
            if kind == "amt":
                if val in ns(ocr[i]).replace(",", "") or (fuzzy_amt and any(edit1(val, n) for n in DOC_NUMS[i])):
                    hit[j] += 1
            elif ns(val) in DOC_NS[i]:
                hit[j] += 1
    return cand, hit


def route(base, fuzzy_amt=False):
    """식별자가 있는 질의는 정확 일치 문서를 최상위로 올림(일치가 없으면 base 그대로)"""
    S = base.copy()
    n_route = 0
    for qi, q in enumerate(queries):
        cand, hit = id_matches(q, fuzzy_amt)
        if cand:
            n_route += 1
            S[qi] += hit * 1000
    return S, n_route


# ---------- 리랭커 ----------
def rerank_scores(model_name, base, topn=20, only_nonid=False, cache={}):
    from fastembed.rerank.cross_encoder import TextCrossEncoder
    key = model_name
    if key not in cache:
        cache[key] = TextCrossEncoder(model_name=model_name)
    m = cache[key]
    S = base.copy()
    t = time.time()
    n = 0
    for qi, q in enumerate(queries):
        if only_nonid and extract_ids(q):
            continue
        top = np.argsort(-base[qi])[:topn]
        sc = list(m.rerank(q, [ocr[ids[j]] for j in top]))
        n += len(top)
        # 상위 topn은 리랭커 점수로 재정렬(나머지보다 항상 위)
        order = np.argsort(-np.array(sc))
        for rank, k in enumerate(order):
            S[qi, top[k]] = 10000 - rank
    print(f"   [{model_name.split('/')[-1]}] {n}쌍 {time.time()-t:.0f}s", flush=True)
    return S


if __name__ == "__main__":
    rer = sys.argv[1:]
    lex = bm25(ocr)
    if os.path.exists("big/dense_ocr_e5.npy"):
        dense = np.load("big/dense_ocr_e5.npy")
    else:
        from fastembed import TextEmbedding
        m = TextEmbedding(model_name="intfloat/multilingual-e5-large")
        Q = np.array(list(m.query_embed(queries)))
        D = np.array(list(m.passage_embed([ocr[i] for i in ids])))
        dense = (Q / np.linalg.norm(Q, axis=1, keepdims=True)) @ (D / np.linalg.norm(D, axis=1, keepdims=True)).T
        np.save("big/dense_ocr_e5.npy", dense)
    hyb = rrf(dense, lex)

    header("OCR 원본 텍스트 — 라우팅")
    show("키워드 단독", lex)
    show("임베딩(e5-large) 단독", dense)
    show("하이브리드 RRF (기준선)", hyb)
    r1, n1 = route(hyb)
    show("하이브리드 + 식별자 라우팅", r1)
    r2, _ = route(hyb, fuzzy_amt=True)
    show("  + 금액 1글자 오차 허용", r2)
    print(f"   (식별자가 추출되어 라우팅된 질의: {n1}/{len(queries)}, 실제 식별자 질의 90개)")
    print("   식별자 추출 오탐(주제 질의 중 라우팅됨):", sum(1 for q, qq in zip(queries, QS) if qq["kind"] == "주제" and extract_ids(q)))

    if rer:
        header("OCR 원본 텍스트 — 리랭커(상위 20개 재정렬)")
        for mn in rer:
            tag = mn.split("/")[-1]
            show(f"하이브리드+리랭커 [{tag}]", rerank_scores(mn, hyb))
            show(f"라우팅 후 주제질의만 리랭커 [{tag}]", route(rerank_scores(mn, hyb, only_nonid=True), True)[0])
