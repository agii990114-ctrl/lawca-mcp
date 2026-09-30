"""참고 서면 마스킹(server/masking.py) 단위 시험: 금액은 복합 표기까지 통째로 가리고, 비슷한 단어·날짜는 건드리지 않는다."""
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "server"))
from masking import mask_text  # noqa: E402


def m(text, **kw):
    return mask_text(text, **kw)[0]


@pytest.mark.parametrize("src", [
    "보증금 2억 8,000만 원", "보증금 2억8,000만원", "1억 2천만 원", "3억원", "5,000만 원", "금 150,000,000원을", "20,000,000원",
    "12억 3,456만 7,890원", "위자료 3천만원씩", "1,000원과", "금 50만 원입니다",
])
def test_amounts_are_masked_completely(src):
    out = m(src)
    assert "[금액]" in out and not re.search(r"\d", out), out       # 숫자가 하나도 남지 않는다(조각 노출 없음)


def test_no_amount_fragment_is_left_next_to_the_placeholder():
    out = m("보증금 2억 8,000만 원 및 연 12%의 지연손해금, 월 차임 150만 원")
    assert out == "보증금 [금액] 및 연 12%의 지연손해금, 월 차임 [금액]"


@pytest.mark.parametrize("src", [
    "원고는 2023. 7. 1. 피고에게 대여하였습니다.", "2025. 7. 1. 원상회복을 구합니다.", "제3호증 원본을 제출합니다.",
    "1. 원고의 청구를 기각한다.", "2025. 6. 30. 만료일에 종료되었습니다.", "원금 및 원리금", "원인 없이 수령한 이득", "연 12%의 비율",
    "2023. 7. 1.부터 2025. 6. 30.까지", "원장을 확인하였습니다.",
])
def test_similar_words_and_dates_are_left_alone(src):
    assert m(src) == src


def test_other_patterns_still_work():
    out = m("주민등록번호 880417-2046318, 전화 010-4827-3906, 02-555-7204, 계좌 1002-347-812645, 메일 a.b@ex.co.kr, 사업자 123-45-67890, 2025가합71234")
    assert "[주민등록번호]" in out and out.count("[전화번호]") == 2 and "[계좌번호]" in out and "[이메일]" in out and "[사업자등록번호]" in out and "[사건번호]" in out
    assert not re.search(r"\d{3,}", out)


def test_party_and_lawyer_names_are_replaced_by_matter_data():
    out = m("원고 방서진과 피고 국태오, 소송대리인 엄도현", plaintiff="방서진", defendant="국태오", attorney="변호사 엄도현")
    assert out == "원고 [원고]과 피고 [피고], 소송대리인 [담당변호사]"
