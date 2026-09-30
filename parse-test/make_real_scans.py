import json, glob, random, textwrap, numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter
docs = []
for f in sorted(glob.glob("real/agent*.json")):
    docs += json.load(open(f, encoding="utf-8"))
json.dump(docs, open("real/all.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
f = ImageFont.truetype("C:/Windows/Fonts/batang.ttc", 28)
W, H = 1240, 1754
for n, x in enumerate(docs):
    rng = random.Random(n + 500); np.random.seed(n + 500)
    img = Image.new("L", (W, H), 255); d = ImageDraw.Draw(img); y = 150
    for l in x["lines"]:
        for w in (textwrap.wrap(l, 42) if l else [""]):
            d.text((120, y), w, font=f, fill=20); y += 42
    assert y < H - 80, (x["id"], y)
    img = img.rotate(rng.uniform(-2.5, 2.5), fillcolor=255, resample=Image.BICUBIC)
    a = np.array(img).astype(np.float32) + np.random.normal(0, rng.uniform(8, 20), (H, W))
    img = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(rng.uniform(0.6, 1.1)))
    img.convert("RGB").save(f"real/png/{x['id']}.png")
print("scans", len(docs))
