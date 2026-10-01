"""법제처 조회 중계(server/law_gateway.py)와 어댑터의 중계 모드 시험: 형식 검증, 응답 최소화, 캐시, 속도 제한, 오류 처리. 네트워크 없이 모의로 시험한다."""
import sys
from datetime import date
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "server"))
import law_api  # noqa: E402
import law_gateway as gw  # noqa: E402


@pytest.fixture(autouse=True)
def reset(monkeypatch):
    gw._cache.clear(); gw._hits.clear()
    monkeypatch.setenv("LAWCA_LAW_API_OC", "dummy-oc")
    monkeypatch.delenv("LAWCA_LAW_GATEWAY_URL", raising=False)
    yield


def client():
    return TestClient(gw.app)


def test_found_case_returns_only_minimal_fields(monkeypatch):
    monkeypatch.setattr(law_api, "find_case", lambda no: {"court": "대법원", "decision_date": date(2005, 2, 17), "label": "물품대금", "link": "…OC=dummy-oc…"})
    r = client().get("/v1/case/2004다59959")
    assert r.status_code == 200
    assert r.json() == {"found": True, "court": "대법원", "label": "물품대금", "decision_date": "2005-02-17"}     # 링크·인증값은 돌려주지 않는다
    assert "dummy-oc" not in r.text


def test_missing_case_returns_found_false(monkeypatch):
    monkeypatch.setattr(law_api, "find_case", lambda no: None)
    assert client().get("/v1/case/2001다3672").json() == {"found": False}


@pytest.mark.parametrize("bad", ["../etc/passwd", "2004다59959%0d%0a", "abc", "2004가단88001", "20004다59959", "2004다", "2004다59959;ls", "2004다5995999999999"])
def test_malformed_case_numbers_are_rejected_before_any_lookup(bad, monkeypatch):
    called = []
    monkeypatch.setattr(law_api, "find_case", lambda no: called.append(no))
    r = client().get(f"/v1/case/{bad}")
    assert r.status_code in (400, 404, 422) and not called


def test_upstream_failure_hides_details(monkeypatch):
    def boom(no): raise law_api.LawApiError("ConnectError https://www.law.go.kr/...OC=dummy-oc")
    monkeypatch.setattr(law_api, "find_case", boom)
    r = client().get("/v1/case/2004다59959")
    assert r.status_code == 502 and "dummy-oc" not in r.text and "law.go.kr" not in r.text


def test_results_are_cached_and_misses_expire_sooner(monkeypatch):
    calls = []
    monkeypatch.setattr(law_api, "find_case", lambda no: calls.append(no) or None)
    c = client()
    c.get("/v1/case/2001다3672"); c.get("/v1/case/2001다3672")
    assert calls == ["2001다3672"]                                       # 두 번째는 캐시
    assert gw.CACHE_TTL_MISS < gw.CACHE_TTL


def test_rate_limit(monkeypatch):
    monkeypatch.setattr(law_api, "find_case", lambda no: None)
    c = client()
    codes = [c.get(f"/v1/case/2004다{59000 + i}").status_code for i in range(gw.RATE_PER_MIN + 5)]
    assert codes.count(200) == gw.RATE_PER_MIN and codes[-1] == 429


def test_not_configured_returns_503(monkeypatch):
    monkeypatch.delenv("LAWCA_LAW_API_OC", raising=False)
    assert client().get("/v1/case/2004다59959").status_code == 503


# ---- 어댑터의 중계 모드(mcp 컨테이너가 쓰는 경로)

def _gateway_client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_adapter_uses_gateway_without_credentials(monkeypatch):
    monkeypatch.delenv("LAWCA_LAW_API_OC", raising=False)
    monkeypatch.setenv("LAWCA_LAW_GATEWAY_URL", "http://law-gateway:8002/")
    assert law_api.configured() is True                                  # 인증값 없이도 중계 주소만 있으면 켜진다
    seen = []

    def handler(req):
        seen.append(str(req.url))
        return httpx.Response(200, json={"found": True, "court": "대법원", "label": "물품대금", "decision_date": "2005-02-17"})
    r = law_api.find_case("2004다59959", client=_gateway_client(handler))
    assert r == {"court": "대법원", "decision_date": date(2005, 2, 17), "label": "물품대금"}
    assert len(seen) == 1 and seen[0].startswith("http://law-gateway:8002/v1/case/")      # 인증값(OC)은 요청에 붙지 않는다
    assert "OC" not in seen[0]


def test_adapter_gateway_not_found_and_errors(monkeypatch):
    monkeypatch.setenv("LAWCA_LAW_GATEWAY_URL", "http://law-gateway:8002")
    assert law_api.find_case("2001다3672", client=_gateway_client(lambda req: httpx.Response(200, json={"found": False}))) is None
    for status in (429, 502, 503, 400):
        with pytest.raises(law_api.LawApiError):
            law_api.find_case("2001다3672", client=_gateway_client(lambda req, s=status: httpx.Response(s, json={})))
    def down(req): raise httpx.ConnectError("no route")
    with pytest.raises(law_api.LawApiError):
        law_api.find_case("2001다3672", client=_gateway_client(down))


# ---- 판례 본문 / 조문 / 검색 엔드포인트

DETAIL = {"case_no": "2004다59959", "court": "대법원", "decision_date": date(2005, 2, 17), "label": "물품대금", "issue": "[1] 채무승인의 방법",
          "holding": "", "ref_statutes": "민법 제168조 제3호", "ref_cases": "", "body": "판례 본문", "serial": "67474"}


def test_case_detail_endpoint_returns_fields_without_raw_links(monkeypatch):
    monkeypatch.setattr(law_api, "direct_case_detail", lambda no: dict(DETAIL))
    r = client().get("/v1/case/2004다59959/detail")
    body = r.json()
    assert r.status_code == 200 and body["found"] is True and body["case"]["decision_date"] == "2005-02-17"
    assert "dummy-oc" not in r.text and "OC=" not in r.text


def test_case_detail_not_found_and_bad_format(monkeypatch):
    monkeypatch.setattr(law_api, "direct_case_detail", lambda no: None)
    assert client().get("/v1/case/2001다3672/detail").json() == {"found": False}
    assert client().get("/v1/case/abc/detail").status_code in (400, 404)


def test_statute_endpoint_found_missing_and_validation(monkeypatch):
    info = {"law": "민법", "article": "제168조", "title": "소멸시효의 중단사유", "text": "제168조(소멸시효의 중단사유)", "paragraphs": [],
            "effective_date": date(2026, 3, 17), "revision": "일부개정", "law_id": "001706"}
    monkeypatch.setattr(law_api, "direct_statute", lambda law, art: dict(info))
    r = client().get("/v1/statute", params={"law": "민법", "article": "제168조"})
    assert r.status_code == 200 and r.json()["statute"]["effective_date"] == "2026-03-17"
    monkeypatch.setattr(law_api, "direct_statute", lambda law, art: {"missing": "article_not_found"})
    assert client().get("/v1/statute", params={"law": "민법", "article": "제9999조"}).json() == {"found": False, "missing": "article_not_found"}
    for law, art in (("민법; rm", "제168조"), ("민법", "168조"), ("민법", "제168항"), ("가", "제1조"), ("../민법", "제1조")):
        assert client().get("/v1/statute", params={"law": law, "article": art}).status_code in (400, 422), (law, art)


def test_search_endpoint_rejects_identifying_queries_at_the_last_gate(monkeypatch):
    called = []
    monkeypatch.setattr(law_api, "direct_search_cases", lambda q, n: called.append(q))
    r = client().get("/v1/cases/search", params={"query": "소외 여은철 매매계약"})
    assert r.status_code == 400 and r.json()["rejected"] is True and "이름" in r.json()["reason"] and not called        # 법제처로 나가지 않는다
    assert client().get("/v1/cases/search", params={"query": "2026가단88001 대여금"}).json()["rejected"] is True
    assert client().get("/v1/cases/search", params={"query": "가" * 60}).status_code == 400 and not called


def test_search_endpoint_accepts_legal_terms_and_clamps_size(monkeypatch):
    seen = []
    def fake(q, n):
        seen.append((q, n))
        return {"total": 18, "items": [{"case_no": "2004다59959", "court": "대법원", "decision_date": date(2005, 2, 17), "label": "물품대금", "serial": "67474"}]}
    monkeypatch.setattr(law_api, "direct_search_cases", fake)
    r = client().get("/v1/cases/search", params={"query": "소멸시효 채무승인", "n": 99})
    assert r.status_code == 200 and r.json()["total"] == 18 and r.json()["items"][0]["decision_date"] == "2005-02-17"
    assert seen == [("소멸시효 채무승인", 10)]                       # n 은 10으로 제한
    client().get("/v1/cases/search", params={"query": "소멸시효 채무승인", "n": 99})
    assert len(seen) == 1                                          # 같은 검색은 캐시


# ---- 어댑터의 중계 모드(새 조회)

def test_adapter_gateway_mode_for_detail_statute_and_search(monkeypatch):
    monkeypatch.setenv("LAWCA_LAW_GATEWAY_URL", "http://law-gateway:8002")
    def handler(req):
        p = req.url.path
        if p.endswith("/detail"):
            return httpx.Response(200, json={"found": True, "case": {"case_no": "2004다59959", "decision_date": "2005-02-17", "issue": "x", "body": "b"}})
        if p == "/v1/statute":
            return httpx.Response(200, json={"found": True, "statute": {"law": "민법", "article": "제168조", "effective_date": "2026-03-17", "paragraphs": []}})
        if p == "/v1/cases/search":
            if "여은철" in req.url.params["query"]:
                return httpx.Response(400, json={"rejected": True, "reason": "사람 이름", "hint": "h"})
            return httpx.Response(200, json={"total": 1, "items": [{"case_no": "2004다59959", "decision_date": "2005-02-17"}]})
        return httpx.Response(404, json={})
    c = _gateway_client(handler)
    assert law_api.case_detail("2004다59959", client=c)["decision_date"] == date(2005, 2, 17)
    assert law_api.statute("민법", "제168조", client=c)["effective_date"] == date(2026, 3, 17)
    assert law_api.search_cases("소멸시효", 5, client=c)["items"][0]["decision_date"] == date(2005, 2, 17)
    assert law_api.search_cases("소외 여은철", 5, client=c)["rejected"] is True
    missing = _gateway_client(lambda req: httpx.Response(200, json={"found": False, "missing": "law_not_found"}))
    assert law_api.statute("없는법", "제1조", client=missing) == {"missing": "law_not_found"}
