"""20쪽 스캔 PDF 합성 → 실제 파싱 경로(kordoc OCR)로 처리 시간·인식 품질 측정.
더미 문장으로 페이지 이미지를 만들고(회전·잡음·번짐 적용) 여러 쪽 PDF로 묶는다. 실제 스캔본이 아니다.
사용: backend/.venv/Scripts/python eval/scan_pdf_test.py [쪽수]
"""
import json, random, sys, textwrap, time
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PAGES = int(sys.argv[1]) if len(sys.argv) > 1 else 20
OUTDIR = ROOT / "eval" / "cwd" / "scan_pdf"
OUTDIR.mkdir(parents=True, exist_ok=True)

docs = json.load(open(ROOT / "parse-test" / "big" / "data.json", encoding="utf-8"))["docs"]
lines = []
for d in docs:
    lines += [l for l in d["lines"] if l.strip()]
font = ImageFont.truetype("C:/Windows/Fonts/batang.ttc", 27)     # 실제 서면 글자 크기에 가깝게(쪽당 약 1,100자)
W, H = 1240, 1754
per_page_lines = 30
wrapped = []
for i in range(0, len(lines), 5):                  # 문단을 이어 붙여 쪽을 실제 서면처럼 빽빽하게 채운다
    wrapped += textwrap.wrap(" ".join(lines[i:i + 5]), 40) or [""]
pages, truth = [], []
for p in range(PAGES):
    rng = random.Random(p); np.random.seed(p)
    chunk = wrapped[(p * per_page_lines) % len(wrapped):][:per_page_lines]
    if len(chunk) < per_page_lines:
        chunk += wrapped[:per_page_lines - len(chunk)]
    img = Image.new("L", (W, H), 255); dr = ImageDraw.Draw(img); y = 150
    for w in chunk:
        dr.text((130, y), w, font=font, fill=25); y += 50
    img = img.rotate(rng.uniform(-1.5, 1.5), fillcolor=255, resample=Image.BICUBIC)
    a = np.array(img).astype(np.float32) + np.random.normal(0, rng.uniform(6, 14), (H, W))
    img = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(rng.uniform(0.5, 0.9)))
    pages.append(img.convert("RGB")); truth.append("".join("".join(chunk).split()))
pdf = OUTDIR / f"scan_{PAGES}p.pdf"
pages[0].save(pdf, save_all=True, append_images=pages[1:], resolution=150)
print(f"PDF 생성: {pdf.name}, {PAGES}쪽, 원문 약 {sum(len(t) for t in truth):,}자(공백 제외), {pdf.stat().st_size/1e6:.1f}MB")

from backend.app.ingest import parse
t0 = time.time()
try:
    text, meta = parse.parse_path(pdf, ".pdf")
except parse.ParseError as e:
    print("파싱 실패:", e, f"({time.time()-t0:.0f}초)")
    sys.exit(1)
sec = time.time() - t0
got = "".join(text.split())
want = "".join(truth)
# 단순 문자 일치율(앞에서부터 순서 유지 최장공통부분수열 대신 문자 다중집합 비교 — 대략적 지표)
from collections import Counter
cw, cg = Counter(want), Counter(got)
recall = sum(min(cw[k], cg[k]) for k in cw) / max(1, len(want))
print(f"파싱 {sec:.0f}초 (쪽당 {sec/PAGES:.1f}초), 추출 {len(got):,}자 / 원문 {len(want):,}자, 문자 재현율 약 {recall:.1%}")
print("제한시간:", parse.config.PARSE_TIMEOUT_SEC, "초 →", "여유 있음" if sec < parse.config.PARSE_TIMEOUT_SEC * 0.6 else "빠듯함/초과 위험")
