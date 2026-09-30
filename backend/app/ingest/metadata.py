"""추출된 텍스트에서 문서 메타데이터를 추정한다(규칙 기반). 추정값은 제안일 뿐이며 사람이 검토·수정할 수 있다."""
import re
from datetime import date

CASE_NO_RE = re.compile(r"\d{4}(?:가합|가단|가소|나|다|라|마|머|드단|드합|르|카합|카단|카기|고단|고합|고정|구합|구단|누)\d{2,7}")
TITLE_KINDS = [("준비서면", "준 비 서 면"), ("답변서", "답 변 서"), ("소장", "소 장"), ("판결", "판결문"),
               ("계약서", "계약서"), ("의견서", "의견서")]
DATE_RE = re.compile(r"(20\d{2})\s*[.\-년]\s*(\d{1,2})\s*[.\-월]\s*(\d{1,2})")


def _ns(s: str) -> str:
    return re.sub(r"[\s#*]+", "", s)


def text_quality(text: str) -> tuple[bool, str]:
    """(양호 여부, 사유). OCR이 깨졌거나 거의 비어 있는 문서를 걸러낸다."""
    body = _ns(text)
    if len(body) < 50:
        return False, "추출된 글자가 거의 없습니다(빈 문서이거나 OCR에 실패했습니다)."
    good = sum(1 for ch in body if ("가" <= ch <= "힣") or ch.isalnum())
    if good / len(body) < 0.6:
        return False, "글자 인식 품질이 낮아 보입니다(OCR 오류 가능). 원본을 확인해 주세요."
    return True, ""


def detect(text: str) -> dict:
    head = _ns(text[:600])
    tail = _ns(text[-500:])
    meta: dict = {}
    # 문서 종류는 '제목 줄'만 보고 판단한다. 본문에 나오는 단어(예: '도급계약서')로 종류를 추정하면 틀린다.
    first_line = next((l for l in text.splitlines() if l.strip()), "")
    title = _ns(first_line)[:40]
    for key, kind in TITLE_KINDS:
        if key in title:
            meta["doc_kind"] = kind
            break
    m = re.search(r"제(\d{1,2})차", head[:120])
    if m:
        meta["round"] = int(m.group(1))
    nums = {m.group(0) for m in CASE_NO_RE.finditer(_ns(text[:1500]))}
    if len(nums) == 1:                       # 여러 개면 관련 사건이 섞인 것이라 자동 지정하지 않는다
        meta["case_no"] = next(iter(nums))
    elif nums:
        meta["case_no_candidates"] = sorted(nums)[:5]
    kind = meta.get("doc_kind")
    if kind == "소 장":
        meta["author_side"] = "원고"
    elif kind == "답 변 서":
        meta["author_side"] = "피고"
    elif kind == "준 비 서 면":
        a, b = "원고소송대리인" in tail or "원고대리인" in tail, "피고소송대리인" in tail or "피고대리인" in tail
        if a != b:
            meta["author_side"] = "원고" if a else "피고"
    # 제출일: 문서 끝의 서명란(귀중·소송대리인·변호사) 바로 앞 날짜만 인정한다.
    # 본문 중간의 사실관계 날짜(대여일 등)를 제출일로 오인해 조용히 저장하는 것을 막는다.
    tail_text = text[-400:]
    dm = None
    for m in DATE_RE.finditer(tail_text):
        after = re.sub(r"\s+", "", tail_text[m.end():m.end() + 100])[:30]     # 날짜 바로 뒤(공백 제외 30자 이내)에 서명 표지가 와야 한다
        if re.search(r"귀중|소송대리인|변호사", after):
            dm = m
    if dm:
        try:
            meta["filed_date"] = date(int(dm.group(1)), int(dm.group(2)), int(dm.group(3))).isoformat()
        except ValueError:
            pass
    return meta
