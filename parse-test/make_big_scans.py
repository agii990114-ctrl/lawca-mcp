import json, random, textwrap, numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter
docs = json.load(open("big/data.json", encoding="utf-8"))["docs"]
f = ImageFont.truetype("C:/Windows/Fonts/batang.ttc", 34)
for n, x in enumerate(docs):
    rng = random.Random(n + 100); np.random.seed(n + 100)
    W, H = 1240, 1754
    img = Image.new("L", (W, H), 255); d = ImageDraw.Draw(img); y = 170
    for l in x["lines"]:
        for w in (textwrap.wrap(l, 32) if l else [""]):
            d.text((140, y), w, font=f, fill=20); y += 60
    img = img.rotate(rng.uniform(-2.5, 2.5), fillcolor=255, resample=Image.BICUBIC)
    a = np.array(img).astype(np.float32) + np.random.normal(0, rng.uniform(8, 20), (H, W))
    img = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(rng.uniform(0.6, 1.1)))
    img.convert("RGB").save(f"big/png/{x['id']}.png")
print("scans", len(docs))
