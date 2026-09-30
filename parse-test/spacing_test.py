import re
from kiwipiepy import Kiwi
kiwi = Kiwi()

ref = """답변서
사 건 2025가합12345 대여금 원고 김철수
피고 주식회사 가나다상사
1. 원고의 청구를 기각한다.
2. 소송비용은 원고가 부담한다.
원고는 2023. 3. 15. 피고에게 금 150,000,000원을
대여하였다고 주장하나 이를 인정할 증거가 없습니다. 상법 제64조에 따른 5년의 상사시효가 적용됩니다.
대법원 2010. 9. 30. 선고 2010다12345 판결 참조.
연락처 010-1234-5678
계좌번호 110-234-567890"""
# 정답에서 원본 이미지에 있던 띄어쓰기 기준 (사 건 -> 사건 으로 통일)
ref = ref.replace("사 건 ", "사건")
ocr = open("out/scan.png.md", encoding="utf-8").read()

def strip_ws(s): return re.sub(r"\s+", "", s)
def space_positions(s):
    """공백 없는 문자열 기준 '이 글자 뒤에 공백이 있다' 인덱스 집합"""
    pos, i = set(), 0
    for ch in s:
        if ch.isspace():
            pos.add(i - 1)
        else:
            i += 1
    return pos

def score(pred, gold):
    assert strip_ws(pred) == strip_ws(gold), "문자 내용이 달라짐"
    p, g = space_positions(pred), space_positions(gold)
    tp = len(p & g); prec = tp / max(len(p), 1); rec = tp / max(len(g), 1)
    f1 = 2 * prec * rec / max(prec + rec, 1e-9)
    return prec, rec, f1

print("문자 내용 일치(공백 무시):", strip_ws(ocr) == strip_ws(ref))
# 문자 내용이 다르면 차이 표시
if strip_ws(ocr) != strip_ws(ref):
    a, b = strip_ws(ocr), strip_ws(ref)
    import difflib
    for op in difflib.SequenceMatcher(None, a, b).get_opcodes():
        if op[0] != "equal": print(op[0], repr(a[op[1]:op[2]]), "->", repr(b[op[3]:op[4]]))
    ocr_cmp = None
else:
    ocr_cmp = ocr

def fix_line(l):
    return kiwi.space(l) if l.strip() else l

variants = {"OCR 원본": ocr}
variants["kiwi.space (줄 단위)"] = "\n".join(fix_line(l) for l in ocr.split("\n"))
variants["kiwi.space (전체)"] = kiwi.space(ocr)
# 식별자 보호: 사건번호/날짜/금액/전화 패턴을 마스킹 후 복원
prot = re.compile(r"\d{4}[가-힣]{1,3}\d+|\d{4}[가-힣]\d+|\d[\d,\.\-]*\d")
def protected(text):
    out = []
    for l in text.split("\n"):
        toks = []
        def sub(m): toks.append(m.group(0)); return f"@@{len(toks)-1}@@"
        m = prot.sub(sub, l)
        m = kiwi.space(m) if m.strip() else m
        m = re.sub(r"@@\s*(\d+)\s*@@", lambda x: toks[int(x.group(1))], m)
        out.append(m)
    return "\n".join(out)
variants["kiwi + 식별자 보호"] = protected(ocr)

print()
for k, v in variants.items():
    try:
        print(f"{k:24s} P/R/F1 = %.2f / %.2f / %.2f" % score(v, ref))
    except AssertionError as e:
        print(k, "ERR", e)
print("\n===== 원본 =====\n" + variants["OCR 원본"])
print("\n===== kiwi 줄 단위 =====\n" + variants["kiwi.space (줄 단위)"])
print("\n===== kiwi + 식별자 보호 =====\n" + variants["kiwi + 식별자 보호"])
