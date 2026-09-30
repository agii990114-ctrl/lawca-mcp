import random, numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from corpus import DOCS
import textwrap
f = ImageFont.truetype("C:/Windows/Fonts/batang.ttc", 34)
for n,(did,lines) in enumerate(DOCS.items()):
    rng = random.Random(n+10); np.random.seed(n+10)
    W,H=1240,1754
    img=Image.new("L",(W,H),255); d=ImageDraw.Draw(img); y=170
    for l in lines:
        for w in (textwrap.wrap(l, 32) if l else [""]):   # 자동 줄바꿈
            d.text((140,y),w,font=f,fill=20); y+=60
    img=img.rotate(rng.uniform(-2,2),fillcolor=255,resample=Image.BICUBIC)
    a=np.array(img).astype(np.float32)+np.random.normal(0,rng.uniform(8,18),(H,W))
    img=Image.fromarray(np.clip(a,0,255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(rng.uniform(0.6,1.0)))
    img.convert("RGB").save(f"corpus/png/{did}.png")
print("ok")
