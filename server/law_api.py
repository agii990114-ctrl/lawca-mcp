"""법제처 국가법령정보 공개 API(DRF)로 판례 사건번호를 조회하는 선택적 외부 확인 수단.

- 켜는 조건: 환경변수 LAWCA_LAW_API_OC(법제처 Open API 인증값)가 있을 때만. 없으면 조회하지 않는다.
- 밖으로 나가는 정보: 사건번호 문자열 하나뿐이다(공개된 판례 번호). 서면 본문·사건 정보·사용자 정보는 보내지 않는다.
- 컨테이너의 mcp 서비스는 인터넷이 막힌 내부망에 있으므로, 운영에서 쓰려면 이 조회만 허용하는 경로(egress 허용 목록)를 따로 열어야 한다.
- 주의: 이 어댑터는 실제 법제처 서비스에 대해 시험하지 못했다(인증값 없음). 응답 구조를 느슨하게 해석하도록 만들었고
  모의 응답 시험만 통과했다. 실제 인증값으로 tests/law_api_live.py 를 먼저 실행해 확인하라.
"""
import os
import re
from datetime import date

import httpx

DEFAULT_BASE = "http://www.law.go.kr/DRF"


class LawApiError(Exception):
    pass


def configured() -> bool:
    return bool(os.environ.get("LAWCA_LAW_API_OC", "").strip())


def _walk(obj):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v)


def _parse_date(s: str | None) -> date | None:
    m = re.search(r"(\d{4})\D?(\d{1,2})\D?(\d{1,2})", s or "")
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def parse_case_response(payload, case_no: str) -> dict | None:
    """검색 응답에서 사건번호가 정확히 같은 항목을 찾는다. 없으면 None."""
    for d in _walk(payload):
        if str(d.get("사건번호", "")).replace(" ", "") == case_no:
            return {"court": d.get("법원명"), "decision_date": _parse_date(d.get("선고일자")),
                    "label": d.get("사건명")}
    return None


def find_case(case_no: str, client: httpx.Client | None = None) -> dict | None:
    """성공하면 항목(dict) 또는 None(조회는 됐으나 없음). 조회 자체가 실패하면 LawApiError."""
    oc = os.environ.get("LAWCA_LAW_API_OC", "").strip()
    if not oc:
        raise LawApiError("LAWCA_LAW_API_OC 가 설정되지 않았습니다.")
    base = os.environ.get("LAWCA_LAW_API_BASE", DEFAULT_BASE).rstrip("/")
    params = {"OC": oc, "target": "prec", "type": "JSON", "nb": case_no}
    own = client is None
    client = client or httpx.Client(timeout=5.0)
    try:
        r = client.get(f"{base}/lawSearch.do", params=params)
        r.raise_for_status()
        return parse_case_response(r.json(), case_no)
    except (httpx.HTTPError, ValueError) as e:      # 네트워크 차단·시간 초과·JSON 아님 모두 '조회 실패'
        raise LawApiError(type(e).__name__) from e
    finally:
        if own:
            client.close()
