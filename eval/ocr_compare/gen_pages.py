"""OCR 비교용 합성 스캔 페이지 생성(쉬운 조건 easy / 어려운 조건 hard). 더미 문장만 사용한다.
사용: python gen_pages.py <작업폴더> [쪽수=10]
출력: <작업폴더>/pages/{easy,hard}/pNN.png, truth_{easy,hard}.json(쪽별 정답, 공백 제거)
"""
import json, random, sys, textwrap
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter

ROOT = Path(__file__).resolve().parents[2]
work = Path(sys.argv[1]); pages = int(sys.argv[2]) if len(sys.argv) > 2 else 10
docs = json.load(open(ROOT / "parse-test" / "big" / "data.json", encoding="utf-8"))["docs"]
lines = [l for d in docs for l in d["lines"] if l.strip()]
wrapped = []
for i in range(0, len(lines), 5):
    wrapped += textwrap.wrap(" ".join(lines[i:i + 5]), 40) or [""]
font = ImageFont.truetype("C:/Windows/Fonts/batang.ttc", 27)
stamp_font = ImageFont.truetype("C:/Windows/Fonts/batang.ttc", 40)
W, H, PER = 1240, 1754, 30


def render(p, cond):
    rng = random.Random(1000 * (cond == "hard") + p); np.random.seed(1000 * (cond == "hard") + p)
    chunk = wrapped[(p * PER) % len(wrapped):][:PER]
    chunk += wrapped[:PER - len(chunk)] if len(chunk) < PER else []
    bg = 255 if cond == "easy" else rng.randint(215, 238)
    img = Image.new("L", (W, H), bg); dr = ImageDraw.Draw(img); y = 150
    for w in chunk:
        dr.text((130, y), w, font=font, fill=25 if cond == "easy" else rng.randint(20, 70)); y += 50
    if cond == "hard":
        cx, cy = rng.randint(900, 1100), rng.randint(1350, 1550)          # 도장 비슷한 원(글자 위에 겹침)
        dr.ellipse([cx - 70, cy - 70, cx + 70, cy + 70], outline=90, width=6)
        dr.text((cx - 20, cy - 24), "인", font=stamp_font, fill=90)
        for _ in range(3):                                                  # 복사기 줄무늬
            yy = rng.randint(100, H - 100); dr.line([(0, yy), (W, yy + rng.randint(-3, 3))], fill=bg - 25, width=rng.randint(1, 3))
    ang = rng.uniform(-1.5, 1.5) if cond == "easy" else rng.uniform(-3.0, 3.0)
    img = img.rotate(ang, fillcolor=bg, resample=Image.BICUBIC)
    sig = rng.uniform(6, 14) if cond == "easy" else rng.uniform(16, 28)
    a = np.array(img).astype(np.float32) + np.random.normal(0, sig, (H, W))
    img = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(rng.uniform(0.5, 0.9) if cond == "easy" else rng.uniform(1.0, 1.5)))
    if cond == "hard":                                                       # 해상도 저하 + JPEG 압축
        img = img.resize((int(W * 0.66), int(H * 0.66)), Image.BILINEAR)
    return img, "".join("".join(chunk).split())


for cond in ("easy", "hard"):
    out = work / "pages" / cond; out.mkdir(parents=True, exist_ok=True); truth = []
    for p in range(pages):
        img, t = render(p, cond)
        if cond == "hard":
            img.save(out / f"p{p:02d}.jpg", quality=40); img = Image.open(out / f"p{p:02d}.jpg"); img.save(out / f"p{p:02d}.png"); (out / f"p{p:02d}.jpg").unlink()
        else:
            img.save(out / f"p{p:02d}.png")
        truth.append(t)
    json.dump(truth, open(work / f"truth_{cond}.json", "w", encoding="utf-8"), ensure_ascii=False)
    print(cond, pages, "쪽", sum(len(t) for t in truth), "자")
