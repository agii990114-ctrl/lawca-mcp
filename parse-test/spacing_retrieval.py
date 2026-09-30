import re, sys
sys.argv=['x']
exec(open("spacing_test.py", encoding="utf-8").read().split("print(\"문자 내용")[0])  # 재사용: kiwi, ref, ocr, score
exec("prot = re.compile(r'\d{4}[가-힣]{1,3}\d+|\d[\d,\.\-]*\d')")
def protected(text):
    out=[]
    for l in text.split("\n"):
        toks=[]
        def sub(m): toks.append(m.group(0)); return f"@@{len(toks)-1}@@"
        m=prot.sub(sub,l); m=kiwi.space(m) if m.strip() else m
        m=re.sub(r"@@\s*(\d+)\s*@@",lambda x:toks[int(x.group(1))],m); out.append(m)
    return "\n".join(out)

docs = {"정답": ref, "OCR 원본": ocr, "kiwi": "\n".join(kiwi.space(l) if l.strip() else l for l in ocr.split("\n")), "kiwi+보호": protected(ocr)}
# 사용자가 실제로 칠 법한 검색어 (정상 띄어쓰기)
queries = ["대여금", "소송비용", "상사시효", "원고의 청구를 기각", "2025가합12345", "2010다12345 판결",
           "소멸시효 상법 제64조", "가나다상사", "계좌번호", "청구를 기각한다", "김철수", "대여하였다고 주장"]

def norm(s): return re.sub(r"\s+","",s)
def ws_hit(q, d):        # 공백 토큰 기준 (키워드 검색이 공백 분리라 가정): 모든 질의어가 문서 토큰에 부분포함
    toks = d.split()
    return all(any(t in tok for tok in toks) for t in q.split())
def ngram_hit(q, d, n=2): # 공백 무시 문자 n-gram 겹침 비율 ≥ 0.9
    qs, ds = norm(q), norm(d)
    g = {qs[i:i+n] for i in range(len(qs)-n+1)}
    dg = {ds[i:i+n] for i in range(len(ds)-n+1)}
    return len(g & dg)/len(g) >= 0.9

print(f"{'질의':24s}" + "".join(f"{k:>10s}" for k in docs))
tot = {k:[0,0] for k in docs}
for q in queries:
    row=[]
    for k,d in docs.items():
        h = ws_hit(q,d); tot[k][0]+=h; row.append("O" if h else "x")
    print(f"{q:24s}" + "".join(f"{r:>10s}" for r in row))
print("\n[공백 토큰 키워드 검색] 적중:", {k:f"{v[0]}/{len(queries)}" for k,v in tot.items()})
ng = {k:sum(ngram_hit(q,d) for q in queries) for k,d in docs.items()}
print("[공백 무시 문자 2-gram 검색] 적중:", {k:f"{v}/{len(queries)}" for k,v in ng.items()})
