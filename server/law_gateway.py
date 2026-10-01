"""판례 사건번호 조회 중계(법제처 Open API). 컨테이너 `law-gateway`로 실행한다.

왜 따로 두나: `mcp` 컨테이너는 사건 DB를 읽으므로 인터넷에 나가지 못하게 해 두었다. 법제처 조회를 위해 `mcp`에
인터넷을 열면 DB 내용이 밖으로 나갈 길이 생긴다. 그래서 인터넷에 나갈 수 있는 이 중계만 따로 두고,
- DB 접근이 전혀 없고(DB 비밀번호를 받지 않는다),
- 법제처 인증값(OC)은 여기에만 있으며,
- 받는 것은 형식이 검증된 값뿐이다: 사건번호, 법령명+조문번호, 검사를 통과한 짧은 검색어(query_guard).
- 법제처 한 곳(www.law.go.kr)에만 요청하고, 돌려주는 것은 필요한 필드뿐이다(응답 원문, 링크, 인증값은 돌려주지 않는다).
`mcp`와는 내부 전용 네트워크(lookup)로만 연결된다.
"""
import os
import re
import sys
import threading
import time
from collections import OrderedDict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from datetime import date  # noqa: E402

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402

import citations  # noqa: E402
import law_api  # noqa: E402
import query_guard  # noqa: E402

CASE_NO = citations.CASE_NO_OK          # 조회 가능한 사건번호 형식은 인용 추출과 같은 정의를 쓴다
CACHE_TTL = 24 * 3600          # 찾은 결과
CACHE_TTL_MISS = 1800          # 못 찾은 결과(법제처 DB에 나중에 올라올 수 있어 짧게)
CACHE_MAX = 2000
RATE_PER_MIN = 60
LAW_NAME = re.compile(r"^[가-힣A-Za-z0-9·ㆍ ]{2,40}$")
ARTICLE = re.compile(r"^제\d{1,4}조(?:의\d{1,2})?$")

app = FastAPI(title="law-gateway", docs_url=None, redoc_url=None, openapi_url=None)
_cache: "OrderedDict[str, tuple[float, dict]]" = OrderedDict()
_hits: list[float] = []
_lock = threading.Lock()


def _rate_ok() -> bool:
    now = time.time()
    with _lock:
        while _hits and now - _hits[0] > 60:
            _hits.pop(0)
        if len(_hits) >= RATE_PER_MIN:
            return False
        _hits.append(now)
        return True


@app.get("/healthz")
def healthz():
    return {"ok": True, "configured": law_api.configured()}


@app.get("/v1/case/{case_no}")
def lookup(case_no: str):
    if not CASE_NO.match(case_no):
        raise HTTPException(400, "사건번호 형식이 올바르지 않습니다.")
    if not law_api.configured():
        raise HTTPException(503, "법제처 인증값이 설정되지 않았습니다.")
    now = time.time()
    with _lock:
        hit = _cache.get(case_no)
        if hit and now < hit[0]:
            _cache.move_to_end(case_no)
            return hit[1]
    if not _rate_ok():
        raise HTTPException(429, "조회 요청이 너무 많습니다.")
    try:
        found = law_api.find_case(case_no)
    except law_api.LawApiError:
        raise HTTPException(502, "법제처 조회에 실패했습니다.")      # 상세 사유(주소·인증값 포함 가능)는 돌려주지 않는다
    body = {"found": False} if found is None else {
        "found": True, "court": found.get("court"), "label": found.get("label"),
        "decision_date": found["decision_date"].isoformat() if found.get("decision_date") else None}
    with _lock:
        _cache[case_no] = (now + (CACHE_TTL if body["found"] else CACHE_TTL_MISS), body)
        while len(_cache) > CACHE_MAX:
            _cache.popitem(last=False)
    return body


def _iso(d: dict | None) -> dict | None:
    if d is None:
        return None
    return {k: (v.isoformat() if isinstance(v, date) else v) for k, v in d.items()}


def _cached(key: str, producer, ttl_hit: int = CACHE_TTL, ttl_miss: int = CACHE_TTL_MISS):
    """캐시 조회 → 없으면 속도 제한 확인 후 조회. producer 는 (본문 dict, 찾았는지) 를 돌려준다."""
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and now < hit[0]:
            _cache.move_to_end(key)
            return hit[1]
    if not _rate_ok():
        raise HTTPException(429, "조회 요청이 너무 많습니다.")
    try:
        body, found = producer()
    except law_api.LawApiError:
        raise HTTPException(502, "법제처 조회에 실패했습니다.")      # 상세 사유(주소·인증값 포함 가능)는 돌려주지 않는다
    with _lock:
        _cache[key] = (now + (ttl_hit if found else ttl_miss), body)
        while len(_cache) > CACHE_MAX:
            _cache.popitem(last=False)
    return body


@app.get("/v1/case/{case_no}/detail")
def case_detail(case_no: str):
    if not CASE_NO.match(case_no):
        raise HTTPException(400, "사건번호 형식이 올바르지 않습니다.")
    if not law_api.configured():
        raise HTTPException(503, "법제처 인증값이 설정되지 않았습니다.")

    def produce():
        d = law_api.direct_case_detail(case_no)
        return ({"found": False} if d is None else {"found": True, "case": _iso(d)}), d is not None
    return _cached("detail:" + case_no, produce)


@app.get("/v1/statute")
def statute(law: str, article: str):
    if not LAW_NAME.match(law) or not ARTICLE.match(article):
        raise HTTPException(400, "법령명 또는 조문 표기가 올바르지 않습니다.")
    if not law_api.configured():
        raise HTTPException(503, "법제처 인증값이 설정되지 않았습니다.")

    def produce():
        d = law_api.direct_statute(law, article)
        if "missing" in d:
            return {"found": False, "missing": d["missing"]}, False
        return {"found": True, "statute": _iso(d)}, True
    return _cached(f"statute:{law}:{article}", produce)


@app.get("/v1/cases/search")
def cases_search(query: str, n: int = 5):
    ok, reason = query_guard.check_query(query)
    if not ok:                                  # 외부로 나가는 마지막 관문: mcp 쪽 검사를 우회해도 여기서 막힌다
        return JSONResponse({"rejected": True, "reason": reason, "hint": query_guard.HINT}, status_code=400)
    if not law_api.configured():
        raise HTTPException(503, "법제처 인증값이 설정되지 않았습니다.")
    n = max(1, min(int(n), 10))

    def produce():
        d = law_api.direct_search_cases(query.strip(), n)
        return {"total": d["total"], "items": [_iso(i) for i in d["items"]]}, d["total"] > 0
    return _cached(f"search:{n}:{query.strip()}", produce, ttl_hit=3600, ttl_miss=600)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=os.environ.get("LAWCA_GATEWAY_HOST", "0.0.0.0"), port=int(os.environ.get("LAWCA_GATEWAY_PORT", "8002")), log_level="warning")
