"""법제처 국가법령정보 공개 API(DRF) 어댑터: 판례 검색·본문, 법령 조문 조회.

- 켜는 조건: LAWCA_LAW_GATEWAY_URL(운영: law-gateway 중계) 또는 LAWCA_LAW_API_OC(법제처 Open API 인증값, 개발·중계 내부용)가 있을 때만.
- 밖으로 나가는 정보: 사건번호, 법령명·조문번호, 검색어(query_guard를 통과한 짧은 법률 용어). 서면 본문·사건 정보·사용자 정보는 보내지 않는다.
- 컨테이너의 mcp 서비스는 인터넷이 막힌 내부망에 있으므로 인증값을 갖지 않고, 인터넷에 나갈 수 있는 law-gateway(server/law_gateway.py)에만 묻는다.
- 응답 구조는 2026-10 실서비스 응답으로 확인했다(server/tests/fixtures/law_api/). 하급심 사건번호는 '서울고등법원-2023-누-36796'
  처럼 돌아오므로 '2023누36796'으로 정규화해 비교한다.
"""
import html
import os
import re
from datetime import date

import httpx

DEFAULT_BASE = "http://www.law.go.kr/DRF"
_CASE_NO_RE = re.compile(r"((?:19|20)\d{2})[-\s]{0,3}((?:재)?[가-힣]{1,3})[-\s]{0,3}(\d{1,7})$")


class LawApiError(Exception):
    pass


def gateway_url() -> str:
    """운영: 인터넷이 막힌 mcp 컨테이너는 인증값 없이 이 중계(law-gateway)에만 묻는다."""
    return os.environ.get("LAWCA_LAW_GATEWAY_URL", "").strip().rstrip("/")


def configured() -> bool:
    return bool(gateway_url() or os.environ.get("LAWCA_LAW_API_OC", "").strip())


# ---------------------------------------------------------------- 공통 해석 도우미
def normalize_case_no(s: str | None) -> str | None:
    """'서울고등법원-2023-누-36796' → '2023누36796', '2004다59959' → '2004다59959'. 해석할 수 없으면 None."""
    m = _CASE_NO_RE.search((s or "").replace(" ", ""))
    return f"{m.group(1)}{m.group(2)}{m.group(3)}" if m else None


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


def _clean(s) -> str:
    """응답의 <br/> 등 태그를 줄바꿈·공백으로 정리한다."""
    t = re.sub(r"<br\s*/?>", "\n", str(s or ""), flags=re.I)
    t = re.sub(r"<[^>]+>", "", t)
    return html.unescape(t).replace(" ", " ").strip()


def _court_of(d: dict) -> str | None:
    c = (d.get("법원명") or "").strip()
    if c:
        return c
    raw = str(d.get("사건번호", ""))
    return raw.split("-")[0] if "-" in raw else None


def _aslist(v):
    return v if isinstance(v, list) else ([] if v in (None, "") else [v])


# ---------------------------------------------------------------- 판례
def parse_case_response(payload, case_no: str) -> dict | None:
    """검색 응답에서 사건번호가 같은 항목을 찾는다(하급심 표기 정규화). 없으면 None."""
    want = normalize_case_no(case_no)
    for d in _walk(payload):
        if "사건번호" in d and normalize_case_no(str(d["사건번호"])) == want:
            return {"court": _court_of(d), "decision_date": _parse_date(d.get("선고일자")), "label": d.get("사건명"),
                    "serial": d.get("판례일련번호")}
    return None


def parse_case_list(payload) -> dict:
    root = payload.get("PrecSearch", payload) if isinstance(payload, dict) else {}
    items = []
    for d in _aslist(root.get("prec")):
        items.append({"case_no": normalize_case_no(str(d.get("사건번호", ""))) or str(d.get("사건번호", "")), "court": _court_of(d),
                      "decision_date": _parse_date(d.get("선고일자")), "label": _clean(d.get("사건명")), "serial": d.get("판례일련번호")})
    try:
        total = int(root.get("totalCnt", len(items)))
    except (TypeError, ValueError):
        total = len(items)
    return {"total": total, "items": items}


def parse_case_detail(payload) -> dict | None:
    root = payload.get("PrecService", payload) if isinstance(payload, dict) else {}
    if not root.get("사건번호"):
        return None
    return {"case_no": normalize_case_no(str(root["사건번호"])) or str(root["사건번호"]), "court": _court_of(root),
            "decision_date": _parse_date(root.get("선고일자")), "label": _clean(root.get("사건명")),
            "issue": _clean(root.get("판시사항")), "holding": _clean(root.get("판결요지")),
            "ref_statutes": _clean(root.get("참조조문")), "ref_cases": _clean(root.get("참조판례")),
            "body": _clean(root.get("판례내용")), "serial": root.get("판례정보일련번호")}


# ---------------------------------------------------------------- 조문
def jo_code(article: str) -> str | None:
    """'제168조' → '016800', '제168조의2' → '016802'"""
    m = re.fullmatch(r"제(\d{1,4})조(?:의(\d{1,2}))?", article.replace(" ", ""))
    return f"{int(m.group(1)):04d}{int(m.group(2) or 0):02d}" if m else None


def _circled(s: str) -> int | None:
    return ord(s[0]) - 0x2460 + 1 if s and "①" <= s[0] <= "⑳" else None


def parse_statute(payload) -> dict:
    """반환: 찾으면 조문 정보 dict, 못 찾으면 {'missing': 'law_not_found' | 'article_not_found'}"""
    if not isinstance(payload, dict) or "법령" not in payload:
        return {"missing": "law_not_found"}
    law = payload["법령"]
    units = [u for u in _aslist((law.get("조문") or {}).get("조문단위")) if u.get("조문여부") == "조문"]
    if not units:
        return {"missing": "article_not_found"}
    u = units[0]
    lines, para_nums = [_clean(u.get("조문내용"))], []
    for h in _aslist(u.get("항")):
        if h.get("항내용"):
            lines.append(_clean(h["항내용"]))
        n = _circled(str(h.get("항번호", "")))
        if n:
            para_nums.append(n)
        for ho in _aslist(h.get("호")):
            lines.append("  " + _clean(ho.get("호내용")))
            for mok in _aslist(ho.get("목")):
                for t in _aslist(mok.get("목내용")):
                    lines.append("    " + _clean(t))
    info = law.get("기본정보") or {}
    return {"law": info.get("법령명_한글"), "article": f"제{u.get('조문번호')}조", "title": u.get("조문제목") or "",
            "text": "\n".join(x for x in lines if x), "paragraphs": para_nums,
            "effective_date": _parse_date(u.get("조문시행일자") or info.get("시행일자")), "revision": info.get("제개정구분"),
            "law_id": info.get("법령ID")}


# ---------------------------------------------------------------- 직접 호출(개발·중계 내부용)
def _oc() -> str:
    oc = os.environ.get("LAWCA_LAW_API_OC", "").strip()
    if not oc:
        raise LawApiError("LAWCA_LAW_API_OC 가 설정되지 않았습니다.")
    return oc


def _direct_get(path: str, params: dict, client: httpx.Client | None = None):
    base = os.environ.get("LAWCA_LAW_API_BASE", DEFAULT_BASE).rstrip("/")
    own = client is None
    client = client or httpx.Client(timeout=8.0)
    try:
        r = client.get(f"{base}/{path}", params={"OC": _oc(), **params})
        r.raise_for_status()
        return r.json()
    except (httpx.HTTPError, ValueError) as e:      # 네트워크 차단·시간 초과·JSON 아님 모두 '조회 실패'
        raise LawApiError(type(e).__name__) from e
    finally:
        if own:
            client.close()


def direct_find_case(case_no: str, client: httpx.Client | None = None) -> dict | None:
    return parse_case_response(_direct_get("lawSearch.do", {"target": "prec", "type": "JSON", "nb": case_no}, client), case_no)


def direct_case_detail(case_no: str, client: httpx.Client | None = None) -> dict | None:
    hit = direct_find_case(case_no, client)
    if not hit or not hit.get("serial"):
        return None
    detail = parse_case_detail(_direct_get("lawService.do", {"target": "prec", "ID": hit["serial"], "type": "JSON"}, client))
    return detail


def direct_statute(law: str, article: str, client: httpx.Client | None = None) -> dict:
    jo = jo_code(article)
    if not jo:
        raise LawApiError("조문 표기가 올바르지 않습니다.")
    return parse_statute(_direct_get("lawService.do", {"target": "law", "type": "JSON", "LM": law, "JO": jo}, client))


def direct_search_cases(query: str, n: int = 5, client: httpx.Client | None = None) -> dict:
    return parse_case_list(_direct_get("lawSearch.do", {"target": "prec", "type": "JSON", "query": query, "search": 2,
                                                       "display": max(1, min(int(n), 10))}, client))


# ---------------------------------------------------------------- 중계(law-gateway) 경유 호출(운영: mcp 컨테이너가 쓰는 경로)
def _gateway_get(path: str, params: dict | None, client: httpx.Client | None) -> dict:
    own = client is None
    client = client or httpx.Client(timeout=10.0)
    try:
        r = client.get(f"{gateway_url()}{path}", params=params)
        if r.status_code == 400 and "rejected" in r.text:
            return r.json()                             # 검색어 거부는 사유를 그대로 전달한다
        if r.status_code != 200:
            raise LawApiError(f"gateway {r.status_code}")
        return r.json()
    except (httpx.HTTPError, ValueError) as e:
        raise LawApiError(type(e).__name__) from e
    finally:
        if own:
            client.close()


def _revive(d: dict | None) -> dict | None:
    """중계가 보낸 JSON의 날짜 문자열을 date로 되돌린다."""
    if d is None:
        return None
    return {k: (_parse_date(v) if k in ("decision_date", "effective_date") and isinstance(v, str) else v) for k, v in d.items()}


def find_case(case_no: str, client: httpx.Client | None = None) -> dict | None:
    """성공하면 항목(dict) 또는 None(조회는 됐으나 없음). 조회 자체가 실패하면 LawApiError."""
    if gateway_url():
        body = _gateway_get(f"/v1/case/{case_no}", None, client)
        return _revive({k: v for k, v in body.items() if k != "found"}) if body.get("found") else None
    return direct_find_case(case_no, client)


def case_detail(case_no: str, client: httpx.Client | None = None) -> dict | None:
    if gateway_url():
        body = _gateway_get(f"/v1/case/{case_no}/detail", None, client)
        return _revive(body["case"]) if body.get("found") else None
    return direct_case_detail(case_no, client)


def statute(law: str, article: str, client: httpx.Client | None = None) -> dict:
    """조문 정보 dict, 또는 {'missing': 'law_not_found' | 'article_not_found'}"""
    if gateway_url():
        body = _gateway_get("/v1/statute", {"law": law, "article": article}, client)
        return _revive(body["statute"]) if body.get("found") else {"missing": body.get("missing", "article_not_found")}
    return direct_statute(law, article, client)


def search_cases(query: str, n: int = 5, client: httpx.Client | None = None) -> dict:
    """{'total', 'items'} 또는 거부 시 {'rejected': True, 'reason': ...}"""
    if gateway_url():
        body = _gateway_get("/v1/cases/search", {"query": query, "n": n}, client)
        if body.get("rejected"):
            return body
        body["items"] = [_revive(i) for i in body.get("items", [])]
        return body
    return direct_search_cases(query, n, client)
