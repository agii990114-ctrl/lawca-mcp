import random
from PIL import Image, ImageDraw, ImageFont, ImageFilter
import numpy as np
random.seed(1)
lines = [
 "답 변 서", "",
 "사 건  2025가합12345 대여금",
 "원 고  김철수",
 "피 고  주식회사 가나다상사", "",
 "1. 원고의 청구를 기각한다.",
 "2. 소송비용은 원고가 부담한다.", "",
 "원고는 2023. 3. 15. 피고에게 금 150,000,000원을",
 "대여하였다고 주장하나 이를 인정할 증거가 없습니다.",
 "상법 제64조에 따른 5년의 상사시효가 적용됩니다.",
 "대법원 2010. 9. 30. 선고 2010다12345 판결 참조.", "",
 "연락처 010-1234-5678",
 "계좌번호 110-234-567890",
]
W,H=1240,1754
img=Image.new("L",(W,H),255)
d=ImageDraw.Draw(img)
f=ImageFont.truetype("C:/Windows/Fonts/batang.ttc",34)
y=180
for l in lines:
    d.text((150,y),l,font=f,fill=20); y+=62
# 도장 흉내
d.ellipse((900,1150,1050,1300),outline=90,width=6)
img=img.rotate(1.6,expand=False,fillcolor=255,resample=Image.BICUBIC)
a=np.array(img).astype(np.float32)
a+=np.random.normal(0,14,a.shape)
img=Image.fromarray(np.clip(a,0,255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(0.8))
img.convert("RGB").save("samples/scan.png")
img.convert("RGB").save("samples/scan.pdf",resolution=150)
