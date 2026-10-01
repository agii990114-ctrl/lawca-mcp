"""청킹. 준비서면은 표준 5개 섹션(+ 3번의 소항목) 단위, 그 밖의 문서는 문단 단위로 묶는다.

섹션 표시(label)는 본문에서 가져오지 않고 고정된 구조 이름만 쓴다(본문이 표시 문자열로 새는 것을 방지).
"""
import re

MAX_CHARS = 800
SPLIT_LIMIT = 800     # 임베딩 모델 입력 한도(512토큰) 안에 들도록: 한글 1자 ≈ 0.5~0.6토큰(숫자·조문이 많아도 800자 ≈ 430~470토큰, eval/long_doc_test.py로 확인)
STD = [("1", "상대방주장의요지", "1. 상대방 주장의 요지"), ("2", "인정및부인", "2. 인정 및 부인"),
       ("3", "쟁점별반박", "3. 쟁점별 반박"), ("4", "입증방법", "4. 입증방법"), ("5", "결론", "5. 결론")]
SUB_RE = re.compile(r"^\s*(?:[#>*\s-]*)([가-하])\s?[.)]\s?")
SENT_SPLIT = re.compile(r"(?<=[.!?。다요음함])\s+")


def _ns(s: str) -> str:
    return re.sub(r"[\s#*]+", "", s)


def _heading(line: str) -> str | None:
    n = _ns(line)
    for num, key, label in STD:
        prefix = f"{num}.{key}"
        if n.startswith(prefix) and len(n) <= len(prefix) + 4:
            return label
    return None


def _split_long(text: str) -> list[str]:
    """MAX_CHARS 안팎으로 문장 경계에서 자른다. 문장이 너무 길면 겹침을 두고 강제 분할."""
    out, buf = [], ""
    for sent in SENT_SPLIT.split(text):
        while len(sent) > MAX_CHARS:
            if buf:
                out.append(buf); buf = ""
            out.append(sent[:MAX_CHARS]); sent = sent[MAX_CHARS - 80:]
        if len(buf) + len(sent) + 1 > MAX_CHARS and buf:
            out.append(buf); buf = sent
        else:
            buf = f"{buf} {sent}".strip()
    if buf:
        out.append(buf)
    return out


def _generic(text: str) -> list[tuple[str, str]]:
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks, buf = [], ""
    for p in paras:
        if len(p) > MAX_CHARS:
            if buf:
                chunks.append(buf); buf = ""
            chunks += _split_long(p)
        elif len(buf) + len(p) + 2 > MAX_CHARS and buf:
            chunks.append(buf); buf = p
        else:
            buf = f"{buf}\n\n{p}".strip()
    if buf:
        chunks.append(buf)
    return [(f"본문 {i + 1}", c) for i, c in enumerate(chunks)]


def chunk_text(text: str) -> list[tuple[str, str]]:
    """[(섹션 표시, 텍스트)]"""
    lines = text.splitlines()
    found = [(i, _heading(l)) for i, l in enumerate(lines)]
    found = [(i, h) for i, h in found if h]
    if len({h for _, h in found}) < 3:
        return _generic(text)
    out: list[tuple[str, str]] = []
    head = "\n".join(l for l in lines[:found[0][0]] if l.strip()).strip()
    if head:
        out.append(("표제", head))
    bounds = found + [(len(lines), None)]
    for (start, label), (end, _) in zip(bounds, bounds[1:]):
        block = lines[start:end]
        if label.startswith("3."):
            cur, buf = label, []
            def flush():
                t = "\n".join(l for l in buf if l.strip()).strip()
                if t and _ns(t) != _ns(label):   # 제목 줄만 있는 조각은 만들지 않는다(제목은 label에 이미 있다)
                    out.append((cur, t))
            for l in block:
                m = SUB_RE.match(l)
                if m and buf:
                    flush(); buf = [l]; cur = f"{label} > {m.group(1)}."
                elif m:
                    buf.append(l); cur = f"{label} > {m.group(1)}."
                else:
                    buf.append(l)
            flush()
        else:
            t = "\n".join(l for l in block if l.strip()).strip()
            if t:
                out.append((label, t))
    final = []
    for label, t in out:      # 너무 긴 구조 단위는 다시 나눈다
        if len(t) > SPLIT_LIMIT:
            final += [(f"{label} ({i + 1})", c) for i, c in enumerate(_split_long(t))]
        else:
            final.append((label, t))
    return final
