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


# ---- 주소·상호·제3자 인명 (규칙 기반, 역할어·호칭이 있을 때만)

@pytest.mark.parametrize("src,gone", [
    ("피고 소유의 서울 마포구 성산로 45, 302호(이하 '이 사건 주택')", ["성산로", "302"]),
    ("경기도 양평군 서종면 문호리 123-4 지상 2층 단독주택", ["문호리", "123-4"]),
    ("서울 마포구 망원동 456-7 소재 1층 카페", ["망원동", "456"]),
    ("부산 연제구 소재 새빛마을 203동 1204호를", ["새빛마을", "203동", "1204호"]),
    ("피고 소유의 대전 서구 갈마로 59 103동 1503호에", ["갈마로", "1503"]),
])
def test_addresses_are_masked(src, gone):
    out = m(src)
    assert "[주소]" in out, out
    assert not any(g in out for g in gone), out


@pytest.mark.parametrize("src", [
    "서울중앙지방법원 제15민사단독 귀중", "서울 마포구에 거주하는 피고", "서울 서초구 소재 법원", "제3조 제2항에 따른 신고",
    "민법 제168조 제3호", "제2호증 원본", "2021. 12. 30. 송금", "대전지방법원 제12민사부",
])
def test_court_names_and_plain_regions_are_not_addresses(src):
    assert m(src) == src


def test_company_names_other_than_parties_are_masked():
    out = m("주식회사 하늘건설과 ㈜새빛유통이 계약했다", plaintiff="가나다")
    assert "하늘건설" not in out and "새빛유통" not in out and out.count("[상호]") == 2, out


@pytest.mark.parametrize("src,gone,keep", [
    ("소외 여은철로부터 주택을 매수하였고", "여은철", "소외 [인명]로부터"),
    ("대표이사 하태율이 확약서를 제시", "하태율", "대표이사 [인명]이"),
    ("지인 박다람에게 전화하였다", "박다람", "지인 [인명]에게"),
    ("원고 담당자 최민수는 이메일로", "최민수", "담당자 [인명]는"),
    ("김철수 씨가 증언하였다", "김철수", "[인명] 씨"),
])
def test_third_party_names_with_role_or_honorific_are_masked(src, gone, keep):
    out = m(src)
    assert gone not in out and keep in out, out


@pytest.mark.parametrize("src", [
    "증인 신문 기일", "담당자 연락처는 별지와 같다", "직원 근로계약 해지", "대표이사 변경 등기", "지인 확인서를 제출",
    "소외 회사의 채무", "직원 정산 내역", "임차인으로서 임대인 지위를 승계", "증인 진술서",
])
def test_common_words_after_role_words_are_not_names(src):
    assert "[인명]" not in m(src), m(src)


def test_name_found_by_role_is_masked_elsewhere_in_the_same_text():
    out = m("소외 여은철은 종전 소유자이다. 여은철에게 보증금을 반환하지 않았다.")
    assert "여은철" not in out and out.count("[인명]") == 2, out


def test_related_names_from_matter_data_are_masked():
    out = m("목격자 서윤아가 현장에 있었고 서윤아의 진술은 일관된다.", related=["서윤아"])
    assert "서윤아" not in out and out.count("[관련자]") == 2, out
    assert mask_text("서윤아", related=["서윤아"])[1].get("당사자·변호사·관련자명") == 1


def test_masking_report_counts_new_categories():
    _, rep = mask_text("피고 소유의 서울 마포구 성산로 45에서 소외 여은철이 주식회사 하늘건설과 계약")
    assert rep.get("주소") == 1 and rep.get("상호") == 1 and rep.get("인명(역할어·호칭)", 0) >= 1, rep
