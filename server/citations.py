"""서면 속 인용(판례·조문)을 뽑아 존재 여부를 대조한다.

검증하는 것은 '그런 판례·조문이 실제로 있는가, 사건번호와 선고일이 서로 맞는가'까지다.
판시 요지가 원문과 같은지, 조문 내용이 현행인지는 이 모듈이 판단하지 못한다(사람이 확인).

출처 세 가지(우선순위 순):
1) 사무소 등록부(verified_citations): 변호사가 확인해 등록한 판례·조문
2) 외부 조회(law_api, 설정된 경우만): 법제처 공개 API로 판례 사건번호 조회
3) 둘 다 없으면 '미확인' — 서면 본문에 넣지 말고 검토 사항으로 돌린다.
"""
import re
from datetime import date

_MARKS = "재다|재두|재도|재마|재나|다|도|두|므|마|스|모|그|누|나|헌가|헌나|헌마|헌바"
_CASE_RE = re.compile(
    r"(?<![\d가-힣])(?:(?P<court>대법원|헌법재판소|[가-힣]{2,6}(?:고등|지방|가정|행정)법원)\s*)?"
    r"(?:(?P<y>(?:19|20)\d{2})\s*\.\s*(?P<m>\d{1,2})\s*\.\s*(?P<d>\d{1,2})\s*\.?\s*(?:선고|자)?\s*)?"
    rf"(?P<no>(?:19|20)\d{{2}}(?:{_MARKS})\d{{1,7}})(?!\d)")
_STATUTE_RE = re.compile(
    r"(?:(?<![가-힣])(?P<law>[가-힣]{1,20}?(?:법|법률))\s*)?"
    r"제(?P<art>\d{1,4})조(?:의(?P<sub>\d{1,2}))?(?:\s*제(?P<para>\d{1,2})항)?(?:\s*제(?P<item>\d{1,2})호)?")
# 직전 조문 바로 뒤에 쉼표·'및' 등으로만 이어진 '제166조'는 같은 법률로 본다(예: 민법 제162조 제1항, 제166조 제1항)
_SEP_RE = re.compile(r"[\s,·、]*(?:및|또는|,|와|과)?[\s,·、]*")


def extract(text: str) -> list[dict]:
    """본문에서 인용을 뽑는다. 같은 인용은 한 번만 돌려준다."""
    found: dict[tuple, dict] = {}
    for m in _CASE_RE.finditer(text):
        no = m.group("no")
        d = None
        if m.group("y"):
            try:
                d = date(int(m.group("y")), int(m.group("m")), int(m.group("d")))
            except ValueError:
                d = None
        key = ("case", no)
        cur = found.get(key)
        item = {"kind": "case", "key": no, "raw": m.group(0).strip(), "court": m.group("court"),
                "decision_date": d}
        if cur is None or (cur["decision_date"] is None and d):
            found[key] = item
    last_law, last_end = None, -10**9
    for m in _STATUTE_RE.finditer(text):
        law = m.group("law")
        if not law:
            law = last_law if _SEP_RE.fullmatch(text[last_end:m.start()]) else None
        else:
            law = re.sub(r"\s+", " ", law)
        # 문맥 없는 '제2조'가 '제2호증', 날짜 등에서 나오지는 않지만 법률명이 전혀 없으면 미특정으로 남긴다.
        art = f"제{m.group('art')}조" + (f"의{m.group('sub')}" if m.group("sub") else "")
        key_txt = f"{law} {art}" if law else art
        item = {"kind": "statute", "key": key_txt, "raw": m.group(0).strip(), "law": law,
                "para": m.group("para"), "item": m.group("item")}
        prev = found.get(("statute", key_txt))
        if prev is None:
            found[("statute", key_txt)] = item
        else:                                # 항·호까지 나온 표기를 우선 보존
            prev.setdefault("paras", [])
            if m.group("para"):
                prev["paras"].append(m.group(0).strip())
        if law:
            last_law, last_end = law, m.end()
    return list(found.values())


def _case_year(case_no: str) -> int:
    return int(case_no[:4])


def evaluate(cit: dict, registry: dict | None, external: dict | None, external_state: str,
             today: date | None = None) -> dict:
    """인용 하나의 판정. registry/external 은 조회 결과 행(없으면 None).

    external_state: 'off'(설정 없음) | 'ok'(조회 성공) | 'error'(조회 실패)
    반환 status: verified_registry | verified_external | date_mismatch | not_found | inconsistent | unverified
    """
    today = today or date.today()
    out = {"kind": cit["kind"], "citation": cit["key"], "raw": cit["raw"]}
    if cit["kind"] == "case":
        d = cit.get("decision_date")
        if d and (d.year < _case_year(cit["key"]) or d > today):
            out.update(status="inconsistent", ok_to_cite=False,
                       reason="선고일이 사건 접수 연도보다 앞서거나 미래입니다. 사건번호 또는 선고일이 틀렸을 가능성이 큽니다.")
            return out
        if d:
            out["decision_date"] = d.isoformat()
        out["holding_checked"] = False
        row, src = (registry, "registry") if registry else (external, "external")
        if row:
            known = row.get("decision_date")
            if d and known and d != known:
                out.update(status="date_mismatch", ok_to_cite=False, source=src,
                           registered_date=known.isoformat(),
                           reason=f"사건번호는 있으나 선고일이 다릅니다(등록/조회: {known.isoformat()}).")
                return out
            out.update(status="verified_registry" if src == "registry" else "verified_external", ok_to_cite=True,
                       source=src, reason="사건번호가 실재합니다. 판시 요지가 원문과 같은지는 확인되지 않았습니다.")
            if row.get("label"):
                out["registered_label"] = row["label"]
            if row.get("summary"):
                out["registered_summary"] = row["summary"]     # 서면의 요지와 대조하는 용도
            if known:
                out.setdefault("registered_date", known.isoformat())
            return out
        if external_state == "ok":
            out.update(status="not_found", ok_to_cite=False,
                       reason="외부 조회에서 이 사건번호가 확인되지 않았습니다. 존재하지 않거나 번호가 틀렸을 수 있으니 본문에서 빼거나 원문을 확인하세요.")
        else:
            out.update(status="unverified", ok_to_cite=False,
                       reason="등록부에 없고 외부 조회도 되지 않았습니다(" +
                              ("설정되지 않음" if external_state == "off" else "조회 실패") +
                              "). 본문에 넣지 말고 검토 사항으로 옮기세요.")
        return out
    # 조문
    if not cit.get("law"):
        out.update(status="unverified", ok_to_cite=False, reason="법률명이 없어 어느 법의 조문인지 특정할 수 없습니다.")
        return out
    if registry:
        note = "등록된 조문입니다. 개정 여부와 항·호의 내용은 등록 본문과 직접 대조하세요."
        out.update(status="verified_registry", ok_to_cite=True, source="registry", reason=note)
        if registry.get("summary"):
            out["registered_text"] = registry["summary"]
        return out
    out.update(status="unverified", ok_to_cite=False, reason="사무소 등록부에 없는 조문입니다. 조문 확인 필요로 표시하세요.")
    return out


def summarize(items: list[dict]) -> dict:
    counts: dict[str, int] = {}
    for i in items:
        counts[i["status"]] = counts.get(i["status"], 0) + 1
    return counts
