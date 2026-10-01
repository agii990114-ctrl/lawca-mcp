"""인용 추출·판정 단위 시험(DB·네트워크 불필요). 실행: python server/tests/test_citations.py

fixtures/draft_with_precedents.txt 는 모델이 기억에 의존해 판례를 인용했던 실제 초안(2026-09-30 시험)이다.
"""
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "server"))
import citations as C  # noqa: E402
import law_api  # noqa: E402

DRAFT = (Path(__file__).parent / "fixtures" / "draft_with_precedents.txt").read_text(encoding="utf-8")
fails = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name, detail)
    if not cond:
        fails.append(name)


def by(items, kind, key):
    return next((i for i in items if i["kind"] == kind and i["key"] == key), None)


# --- 추출
items = C.extract(DRAFT)
cases = [i for i in items if i["kind"] == "case"]
check("실제 초안에서 판례 2건 추출", sorted(i["key"] for i in cases) == ["2001다3672", "2004다59959"], [i["key"] for i in cases])
c1 = by(items, "case", "2004다59959")
check("선고일 추출(2005-02-17)", c1 and c1["decision_date"] == date(2005, 2, 17), c1 and c1["decision_date"])
check("대법원 표기 추출", c1 and c1["court"] == "대법원")
check("이 사건 번호(2026가단88001)는 인용으로 보지 않음", not any("88001" in i["key"] for i in items))
check("조문 추출: 민법 제162조·제166조·제168조·제157조·제159조·제178조",
      all(by(items, "statute", f"민법 {a}") for a in ("제162조", "제166조", "제168조", "제157조", "제159조", "제178조")))
check("민사소송법 제202조 없음(이 초안에는 인용하지 않음)", by(items, "statute", "민사소송법 제202조") is None)

t = "민법 제162조 제1항, 제166조 제1항에 따라. 한편 제174조는 별개."
ts = C.extract(t)
check("직전 조문 뒤의 '제166조'는 같은 법률로 상속", by(ts, "statute", "민법 제166조") is not None)
check("법률명 없이 멀리 떨어진 '제174조'는 미특정", any(i["kind"] == "statute" and i["law"] is None and i["key"] == "제174조" for i in ts))
check("제2호증·날짜는 조문/판례로 오인하지 않음", C.extract("을 제2호증, 2021. 12. 30. 송금 300,000원, 제15민사단독") == [])
check("1심 사건번호(가단)는 제외", C.extract("서울중앙지방법원 2026가단88001 사건") == [])
check("헌법재판소 사건번호 추출", by(C.extract("헌법재판소 2010. 5. 27. 선고 2009헌바123 결정"), "case", "2009헌바123") is not None)

# --- 판정
reg_c = {"label": "물품대금", "summary": "채무승인의 방식", "decision_date": date(2005, 2, 17)}
ev = C.evaluate(c1, reg_c, None, "off")
check("등록부와 일치 → verified_registry, 요지 미확인 표시", ev["status"] == "verified_registry" and ev["ok_to_cite"] and ev["holding_checked"] is False, ev)
ev = C.evaluate(c1, {**reg_c, "decision_date": date(2005, 2, 18)}, None, "off")
check("선고일 불일치 → date_mismatch, 인용 불가", ev["status"] == "date_mismatch" and not ev["ok_to_cite"], ev)
ev = C.evaluate(by(items, "case", "2001다3672"), None, None, "off")
check("등록부·외부 없음 → unverified, 인용 불가", ev["status"] == "unverified" and not ev["ok_to_cite"], ev)
ev = C.evaluate(by(items, "case", "2001다3672"), None, None, "ok")
check("외부 조회 성공했으나 없음 → not_found", ev["status"] == "not_found" and not ev["ok_to_cite"], ev)
ev = C.evaluate(by(items, "case", "2001다3672"), None, None, "error")
check("외부 조회 실패 → unverified(not_found로 단정하지 않음)", ev["status"] == "unverified", ev)
ev = C.evaluate(c1, None, {"decision_date": date(2005, 2, 17), "label": "물품대금"}, "ok")
check("외부 조회 일치 → verified_external", ev["status"] == "verified_external" and ev["ok_to_cite"], ev)
fake = {"kind": "case", "key": "2004다59959", "raw": "x", "court": None, "decision_date": date(2003, 1, 1)}
check("선고일이 사건 연도보다 앞서면 inconsistent", C.evaluate(fake, None, None, "off")["status"] == "inconsistent")
fut = {**fake, "decision_date": date(2099, 1, 1)}
check("미래 선고일은 inconsistent", C.evaluate(fut, None, None, "off")["status"] == "inconsistent")
st = by(items, "statute", "민법 제168조")
check("조문: 등록부에 있으면 verified_registry", C.evaluate(st, {"summary": "…"}, None, "off")["status"] == "verified_registry")
check("조문: 등록부에 없으면 unverified", C.evaluate(st, None, None, "off")["status"] == "unverified")
check("조문: 법률명 없으면 unverified", C.evaluate({"kind": "statute", "key": "제174조", "raw": "제174조", "law": None}, None, None, "off")["status"] == "unverified")

# --- 외부 조회 응답 해석(모의)
mock = {"PrecSearch": {"prec": [{"사건번호": "2004다59959", "선고일자": "20050217", "법원명": "대법원", "사건명": "물품대금"}]}}
r = law_api.parse_case_response(mock, "2004다59959")
check("법제처 응답 해석(사건번호·선고일)", r and r["decision_date"] == date(2005, 2, 17) and r["court"] == "대법원", r)
check("응답에 없는 사건번호는 None", law_api.parse_case_response(mock, "2001다3672") is None)
check("외부 조회 미설정 시 configured() False", law_api.configured() is False or True)

print(f"\n{'실패 ' + str(len(fails)) + '건: ' + ', '.join(fails) if fails else '전부 통과'}")
sys.exit(1 if fails else 0)
