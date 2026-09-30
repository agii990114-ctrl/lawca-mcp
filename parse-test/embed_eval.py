import sys, re, math, time, numpy as np
from collections import Counter
from fastembed import TextEmbedding
from kiwipiepy import Kiwi
from corpus import DOCS, QUERIES
kiwi = Kiwi()
ids = list(DOCS)
clean = {i: "\n".join(DOCS[i]) for i in ids}
ocr   = {i: open(f"corpus/ocr/{i}.md", encoding="utf-8").read() for i in ids}
kiwi_t = {i: "\n".join(kiwi.space(l) if l.strip() else l for l in ocr[i].split("\n")) for i in ids}
variants = {"정답(깨끗한 텍스트)": clean, "OCR 원본": ocr, "OCR+kiwi 띄어쓰기": kiwi_t}
NL = QUERIES[:16]; EX = QUERIES[16:]

def metrics(rank_lists, qs):
    r1=r3=mrr=0
    for ranks,(q,gold) in zip(rank_lists, qs):
        pos = ranks.index(gold)+1
        r1+= pos==1; r3+= pos<=3; mrr+=1/pos
    n=len(qs); return r1/n, r3/n, mrr/n

def ranks_from_scores(S):  # S: [nq, nd]
    return [[ids[j] for j in np.argsort(-row)] for row in S]

# ---- 문자 n-gram 키워드(공백 무시) 검색 ----
def grams(t, ns=(2,3)):
    t = re.sub(r"\s+","",t); c=Counter()
    for n in ns: c.update(t[i:i+n] for i in range(len(t)-n+1))
    return c
def bm25_scores(docs, queries, k1=1.2, b=0.75):
    dg=[grams(docs[i]) for i in ids]; N=len(dg); avg=sum(sum(g.values()) for g in dg)/N
    df=Counter(); [df.update(g.keys()) for g in dg]
    S=np.zeros((len(queries),N))
    for qi,q in enumerate(queries):
        for g in grams(q):
            if g not in df: continue
            idf=math.log(1+(N-df[g]+.5)/(df[g]+.5))
            for j,dgc in enumerate(dg):
                tf=dgc.get(g,0)
                if tf: S[qi,j]+=idf*tf*(k1+1)/(tf+k1*(1-b+b*sum(dgc.values())/avg))
    return S
def rrf(*Ss, k=60):
    out=np.zeros_like(Ss[0])
    for S in Ss:
        order=np.argsort(-S,axis=1)
        for qi in range(S.shape[0]):
            for r,j in enumerate(order[qi]): out[qi,j]+=1/(k+r+1)
    return out

queries=[q for q,_ in QUERIES]
def report(tag, S_by_variant):
    print(f"\n### {tag}")
    print(f"{'문서 텍스트':22s} | 자연어16 R@1 R@3 MRR | 번호·이름4 R@1 R@3 MRR")
    for vname,S in S_by_variant.items():
        rl=ranks_from_scores(S)
        a=metrics(rl[:16],NL); e=metrics(rl[16:],EX)
        print(f"{vname:22s} |        {a[0]:.2f} {a[1]:.2f} {a[2]:.2f} |        {e[0]:.2f} {e[1]:.2f} {e[2]:.2f}")

lex={v: bm25_scores(d, queries) for v,d in variants.items()}
report("문자 2-3gram 키워드(BM25, 공백 무시)", lex)

for model in sys.argv[1:]:
    t=time.time(); m=TextEmbedding(model_name=model)
    print(f"\n[모델 로드 {model}] {time.time()-t:.0f}s")
    Q=np.array(list(m.query_embed(queries)))
    dense={}
    for v,d in variants.items():
        D=np.array(list(m.passage_embed([d[i] for i in ids])))
        Qn=Q/np.linalg.norm(Q,axis=1,keepdims=True); Dn=D/np.linalg.norm(D,axis=1,keepdims=True)
        dense[v]=Qn@Dn.T
    report(f"임베딩 {model}", dense)
    report(f"하이브리드(RRF) 임베딩+n-gram — {model}", {v: rrf(dense[v], lex[v]) for v in variants})
