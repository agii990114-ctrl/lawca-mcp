"""긴 문서를 AI에게 나누어 주기 위한 분할 도우미.

20쪽(약 3만 자)짜리 서면을 get_document가 통째로 돌려주면 클라이언트의 도구 응답 크기 한도에 걸리거나
AI의 작업 공간을 한 번에 차지한다. 그래서 (1) 섹션 단위 조회, (2) 일정 글자 수 단위 분할(part)을 제공한다.
마스킹은 문서 전체에 먼저 적용한 뒤 자른다(경계에서 값이 잘려 마스킹을 피하지 않도록).
"""

PART_CHARS = 8000     # 한 번에 돌려주는 최대 글자 수(한글 기준 대략 5천~8천 토큰)


def split_parts(text: str, size: int = PART_CHARS) -> list[str]:
    """줄 경계에서 size 글자 안팎으로 나눈다. 한 줄이 size보다 길면 그 줄만 강제로 자른다."""
    parts: list[str] = []
    cur: list[str] = []
    n = 0
    for line in text.split("\n"):
        if len(line) > size:
            if cur:
                parts.append("\n".join(cur)); cur, n = [], 0
            parts += [line[i:i + size] for i in range(0, len(line), size)]
            continue
        if cur and n + len(line) + 1 > size:
            parts.append("\n".join(cur)); cur, n = [], 0
        cur.append(line); n += len(line) + 1
    if cur:
        parts.append("\n".join(cur))
    return parts or [""]


def pick_part(text: str, part: int | None, size: int = PART_CHARS) -> tuple[str, int, int]:
    """(해당 부분 텍스트, 현재 part 번호(1부터), 전체 part 수). part 가 범위를 벗어나면 ValueError."""
    parts = split_parts(text, size)
    p = 1 if part is None else int(part)
    if p < 1 or p > len(parts):
        raise ValueError(f"part는 1~{len(parts)} 범위여야 합니다.")
    return parts[p - 1], p, len(parts)
