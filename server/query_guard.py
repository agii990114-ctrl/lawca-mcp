"""외부(법제처)로 나가는 판례 검색어를 검사한다.

검색어는 AI가 만든 자유 문장이라 사건 정보가 섞여 나갈 수 있다. 도구 설명으로 AI가 법률 쟁점·용어만 쓰게 하지만,
그것은 요청일 뿐이라 서버가 한 번 더 확인한다. 걸리면 거부하고 이유를 알려 AI가 고쳐 쓰게 한다.

잡는 것: 마스킹 규칙에 걸리는 값(번호·금액·주소·상호·역할어가 붙은 이름·호칭), 너무 긴 문장, 줄바꿈.
못 잡는 것: 법률 용어로 풀어 쓴 사건 특징(예: "대표이사가 개인 계좌로 횡령한 건설사 하도급"). 이 부분은 도구 설명이 막는 영역이다.
이 검사는 mcp 서버(AI에게 즉시 피드백)와 law-gateway(외부로 나가는 마지막 관문) 양쪽에서 같은 함수를 쓴다.
"""
import re

from masking import mask_text

MAX_LEN = 50
MIN_LEN = 2

_LABELS = {"[주민등록번호]": "주민등록번호", "[이메일]": "이메일", "[사건번호]": "사건번호", "[전화번호]": "전화번호",
           "[사업자등록번호]": "사업자등록번호", "[계좌번호]": "계좌번호", "[주소]": "주소", "[상호]": "상호",
           "[금액]": "금액", "[인명]": "사람 이름"}

HINT = "법률 쟁점·용어만으로 다시 쓰세요(예: '소멸시효 채무승인 문자메시지'). 당사자·상호·주소·사건번호·금액·구체적 사실관계는 넣지 마세요."


def check_query(query: str) -> tuple[bool, str]:
    """(통과 여부, 거부 사유). 통과하면 사유는 빈 문자열."""
    q = (query or "").strip()
    if len(q) < MIN_LEN:
        return False, "검색어가 너무 짧습니다."
    if len(q) > MAX_LEN:
        return False, f"검색어가 {MAX_LEN}자를 넘습니다({len(q)}자). 핵심 법률 용어만 남겨 짧게 쓰세요."
    if "\n" in query or "\r" in query:
        return False, "검색어에 줄바꿈이 있습니다. 한 줄의 짧은 법률 용어로 쓰세요."
    masked, report = mask_text(q)
    if masked != q:
        found = sorted({_LABELS[t] for t in _LABELS if t in masked})
        what = ", ".join(found) if found else "개인·사건 식별 정보"
        return False, f"검색어에 {what}로 보이는 값이 있습니다."
    if re.search(r"\d{4}\s*[가-힣]{1,3}\s*\d{2,}", q):
        return False, "검색어에 사건번호로 보이는 값이 있습니다(사건번호는 get_case로 조회하세요)."
    return True, ""
