"""법제처 응답 해석·검색어 검사·조문/요지 판정 단위 시험(네트워크 없음).
응답 구조는 2026-10 실서비스 응답을 저장한 server/tests/fixtures/law_api/ 로 확인한다(인증값은 <OC>로 가려져 있다)."""
import json
import sys
from datetime import date
from pathlib import Path

import pytest

SERVER = Path(__file__).resolve().parents[2] / "server"
sys.path.insert(0, str(SERVER))
import citations as C  # noqa: E402
import law_api as L  # noqa: E402
import query_guard as Q  # noqa: E402

FIX = SERVER / "tests" / "fixtures" / "law_api"


def fx(name):
    return json.loads((FIX / f"{name}.json").read_text(encoding="utf-8"))


# ---- 사건번호 정규화 (하급심 표기 버그의 회귀 방지)
@pytest.mark.parametrize("raw,want", [
    ("서울고등법원-2023-누-36796", "2023누36796"), ("포항지원-2024-가단-103753", "2024가단103753"), ("2004다59959", "2004다59959"),
    ("대법원 2005. 2. 17. 선고 2004다59959", "2004다59959"), ("2022재다1234", "2022재다1234"), ("abc", None), ("", None),
])
def test_normalize_case_no(raw, want):
    assert L.normalize_case_no(raw) == want


def test_lower_court_case_is_found_despite_different_number_format():
    hit = L.parse_case_response(fx("prec_search_nb_lower"), "2023누36796")       # 응답은 '서울고등법원-2023-누-36796'
    assert hit and hit["court"] == "서울고등법원" and hit["decision_date"] == date(2024, 5, 29)


def test_supreme_court_case_and_missing_case():
    hit = L.parse_case_response(fx("prec_search_nb_supreme"), "2004다59959")
    assert hit == {"court": "대법원", "decision_date": date(2005, 2, 17), "label": "물품대금", "serial": "67474"}
    assert L.parse_case_response(fx("prec_search_nb_none"), "2001다3672") is None


def test_case_detail_fields_and_html_cleanup():
    d = L.parse_case_detail(fx("prec_service_2004da59959"))
    assert d["case_no"] == "2004다59959" and d["court"] == "대법원" and d["decision_date"] == date(2005, 2, 17)
    assert "시효중단사유로서의 채무승인의 방법" in d["issue"] and "<br" not in d["issue"] and "민법 제168조 제3호" in d["ref_statutes"]
    assert len(d["body"]) > 1000 and "<br" not in d["body"]
    assert L.parse_case_detail({"PrecService": {}}) is None


def test_case_list():
    r = L.parse_case_list(fx("prec_search_query"))
    assert r["total"] > 100 and 1 <= len(r["items"]) <= 5
    assert all(i["case_no"] and i["serial"] for i in r["items"])
    assert "2025가단105666" in [i["case_no"] for i in r["items"]] or any(i["case_no"].startswith("20") for i in r["items"])


# ---- 조문
def test_statute_with_paragraphs_list_shape():
    s = L.parse_statute(fx("law_162"))                      # 조문단위가 리스트(장 제목 + 조문)
    assert s["law"] == "민법" and s["article"] == "제162조" and s["paragraphs"] == [1, 2]
    assert "10년간 행사하지 아니하면" in s["text"] and s["effective_date"] == date(2026, 3, 17)


def test_statute_with_items_and_without_paragraph_numbers():
    s = L.parse_statute(fx("law_168"))
    assert s["paragraphs"] == [] and "3. 승인" in s["text"] and "중단된다" in s["text"]
    assert "최고는 6월내에" in L.parse_statute(fx("law_174"))["text"]


def test_missing_statute_distinguishes_law_from_article():
    assert L.parse_statute(fx("law_missing_article")) == {"missing": "article_not_found"}
    assert L.parse_statute(fx("law_missing_law")) == {"missing": "law_not_found"}


@pytest.mark.parametrize("art,code", [("제168조", "016800"), ("제168조의2", "016802"), ("제1조", "000100"), ("168조", None), ("제168항", None)])
def test_jo_code(art, code):
    assert L.jo_code(art) == code


# ---- 조문 판정(법제처 결과 반영)
def stat_cit(para=None):
    return {"kind": "statute", "key": "민법 제162조", "raw": "민법 제162조", "law": "민법", "para": para, "item": None}


def test_statute_verified_external_with_text_and_date():
    ext = L.parse_statute(fx("law_162"))
    r = C.evaluate(stat_cit("1"), None, ext, "ok")
    assert r["status"] == "verified_external" and r["ok_to_cite"] and r["effective_date"] == "2026-03-17" and "10년간" in r["official_text"]


def test_statute_with_nonexistent_paragraph_is_not_found():
    r = C.evaluate(stat_cit("3"), None, L.parse_statute(fx("law_162")), "ok")        # 제162조에는 제1·2항뿐
    assert r["status"] == "not_found" and not r["ok_to_cite"] and "제3항" in r["reason"]


def test_missing_article_is_not_found_but_unknown_law_stays_unverified():
    cit = {"kind": "statute", "key": "민법 제9999조", "raw": "민법 제9999조", "law": "민법", "para": None, "item": None}
    assert C.evaluate(cit, None, {"missing": "article_not_found"}, "ok")["status"] == "not_found"
    assert C.evaluate(cit, None, {"missing": "law_not_found"}, "ok")["status"] == "unverified"       # 약칭일 수 있어 '없음'으로 단정하지 않는다
    assert C.evaluate(cit, None, None, "error")["status"] == "unverified"


def test_registry_takes_priority_over_external_for_statutes():
    r = C.evaluate(stat_cit(), {"summary": "등록 본문"}, L.parse_statute(fx("law_162")), "ok")
    assert r["status"] == "verified_registry" and r["registered_text"] == "등록 본문"


# ---- 판례 요지 대조(휴리스틱)
RAW = "대법원 2005. 2. 17. 선고 2004다59959"


def overlap_of(sentence):
    d = L.parse_case_detail(fx("prec_service_2004da59959"))
    return C.enrich_case({"status": "verified_external", "ok_to_cite": True}, d, sentence, RAW)


def test_accurate_claims_pass_and_unrelated_claims_are_flagged():
    good = overlap_of(RAW + " 판결은, 시효중단 사유로서의 채무 승인은 시효이익을 받을 채무자가 상대방의 권리가 존재함을 인식하고 있다는 뜻을 표시함으로써 성립하고, 특별한 방식을 요하지 않는다고 판시하였습니다. 끝.")
    burden = overlap_of(RAW + " 판결에 따르면 시효중단사유인 채무승인의 증명책임은 채권자에게 있습니다. 그러므로 원고가 증명해야 합니다.")
    bad = overlap_of(RAW + " 판결은 임대차보증금 반환채무와 목적물 인도의무는 동시이행관계에 있다고 판시하였습니다. 따라서 임차인은 인도를 거절할 수 있습니다.")
    assert "holding_warning" not in good and good["holding_overlap"] >= 0.5
    assert "holding_warning" not in burden
    assert "holding_warning" in bad and bad["holding_overlap"] < C.LOW_OVERLAP
    assert good["official"]["issue"].startswith("[1] 시효중단사유로서의 채무승인의 방법")


def test_no_claim_sentence_means_no_overlap_judgement():
    r = overlap_of(RAW + " 판결을 참고하세요.")
    assert "holding_overlap" not in r and "holding_warning" not in r and r["official"]["label"] == "물품대금"


def test_known_limit_same_field_but_different_holding_is_not_caught():
    r = overlap_of(RAW + " 판결은 소멸시효 완성 후 채무를 승인하면 시효이익을 포기한 것으로 본다고 판시하였습니다. 이 경우 다시 시효가 진행하지 않습니다.")
    assert "holding_overlap" in r          # 같은 분야의 조금 다른 요지는 점수가 중간이라 구분하지 못한다(문서화된 한계)


# ---- 검색어 검사
@pytest.mark.parametrize("q", ["소멸시효 채무승인 문자메시지", "유치권 하도급대금", "임대차 보증금 반환 동시이행", "부당해고 구제신청", "사해행위취소 수익자 악의"])
def test_legal_terms_pass(q):
    assert Q.check_query(q) == (True, "")


@pytest.mark.parametrize("q,word", [
    ("2026가단88001 대여금", "사건번호"), ("서울 마포구 성산로 45 건물", "주소"), ("5천만 원 대여금 반환", "금액"), ("주식회사 하늘건설 하도급", "상호"),
    ("소외 여은철 매매계약", "이름"), ("010-1234-5678", "전화"), ("연락처 abc@ex.co.kr", "이메일"), ("김철수 씨 대여금", "이름"),
])
def test_identifying_values_are_rejected_with_reason(q, word):
    ok, reason = Q.check_query(q)
    assert not ok and word in reason, reason


def test_length_newline_and_empty_are_rejected():
    assert not Q.check_query("소멸시효" * 20)[0]
    assert "줄바꿈" in Q.check_query("소멸시효\n채무승인")[1]
    assert not Q.check_query("")[0] and not Q.check_query("가")[0]


def test_known_limit_bare_names_and_described_facts_are_not_detected():
    assert Q.check_query("홍길동 대여금")[0] is True                        # 역할어·호칭 없는 이름은 규칙으로 못 잡는다(도구 설명이 막는 영역)
    assert Q.check_query("대표이사가 개인 계좌로 횡령한 하도급")[0] is True    # 법률 용어로 풀어 쓴 사건 특징도 통과한다
