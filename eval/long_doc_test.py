"""긴 서면(약 20쪽) 처리 시험: 청킹 → 임베딩 시간·토큰 길이 → get_document 응답 크기.
더미 서면들을 이어 붙여 약 3만 자짜리 준비서면을 합성한다(실제 사건 자료 아님). DB에는 쓰지 않는다.
사용: backend/.venv/Scripts/python eval/long_doc_test.py [글자수]
"""
import re, sys, time, statistics
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "server"))
import psycopg
import db
from backend.app.ingest import chunking, embed

TARGET = int(sys.argv[1]) if len(sys.argv) > 1 else 30000
LONGPARA = len(sys.argv) > 2 and sys.argv[2] == "long"
c = psycopg.connect(db._BASE + f" user=lawca_owner password={db.ENV['LAWCA_OWNER_PASSWORD']}")
texts = [r[0] for r in c.execute("select full_text from documents where kind like '준%' order by doc_id")]

def section(t, n):
    m = re.search(rf"(?m)^\s*{n}\.\s", t)
    return t[m.start():] if m else ""

def build(target):
    parts = {"1": [], "2": [], "3": [], "4": [], "5": []}
    total, i = 0, 0
    subs = "가나다라마바사아자차카타파하"
    k = 0
    while total < target:
        t = texts[i % len(texts)]; i += 1
        body = re.sub(r"(?m)^\s*[1-5]\.\s.*$", "", t)                # 원래 표제 제거
        chunk = body.strip()
        if LONGPARA:
            chunk = " ".join(chunk.split())          # 줄바꿈 없는 긴 문단(실제 서면처럼)
        parts["3"].append(f"{subs[k % len(subs)]}. 쟁점 {k + 1}\n{chunk}"); k += 1
        total += len(chunk)
    head = "준 비 서 면\n사 건 2026가합00000 손해배상(기)\n원 고 [원고]\n피 고 [피고]\n\n"
    return (head + "1. 상대방 주장의 요지\n" + texts[0][:900] + "\n\n2. 인정 및 부인\n" + texts[1][:700] +
            "\n\n3. 쟁점별 반박\n" + "\n\n".join(parts["3"]) + "\n\n4. 입증방법\n을 제1호증 ...\n\n5. 결론\n" + texts[2][:400])

doc = build(TARGET)
print(f"합성 문서: {len(doc):,}자 (A4 약 {len(doc)/1500:.0f}쪽 분량)")
t0 = time.time()
chunks = chunking.chunk_text(doc)
print(f"청킹: {len(chunks)}개, {time.time()-t0:.2f}초; 청크 글자수 평균 {statistics.mean(len(x[1]) for x in chunks):.0f}, 최대 {max(len(x[1]) for x in chunks)}")
secs = [s for s, _ in chunks]
print("표준 표제 인식:", [s for s in dict.fromkeys(secs)][:8], "…")

m = embed._get()
tok = getattr(m.model, "tokenizer", None)
lens = []
if tok is not None:
    for _, t in chunks:
        lens.append(len(tok.encode("passage: " + t).ids))
    print(f"토큰 길이: 평균 {statistics.mean(lens):.0f}, 최대 {max(lens)}, 512 초과 {sum(1 for x in lens if x > 512)}개 / {len(lens)}")
else:
    print("토큰 길이 확인 불가(토크나이저 접근 실패)")

texts_only = [t for _, t in chunks]
t0 = time.time()
vecs = embed.embed_passages(texts_only[:20])
per = (time.time() - t0) / 20
print(f"임베딩: 청크당 {per:.2f}초 → 문서 1건({len(chunks)}청크) 약 {per*len(chunks)/60:.1f}분, 서면 100건이면 약 {per*len(chunks)*100/3600:.1f}시간(이 PC의 CPU 기준)")

if tok is not None:
    try:
        tok.no_truncation()
        full = [len(tok.encode("passage: " + t).ids) for _, t in chunks]
        over = [(n, len(t)) for n, (_, t) in zip(full, chunks) if n > 512]
        print(f"잘림 없이 센 토큰: 최대 {max(full)}, 512 초과(=임베딩 때 뒤가 잘림) {len(over)}개 / {len(full)}; 예: {over[:5]}")
    except Exception as e:
        print("잘림 검사 실패:", e)
