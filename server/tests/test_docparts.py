"""긴 문서 분할 도우미 단위 시험. 실행: python server/tests/test_docparts.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import docparts as D

fails = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name, detail)
    if not cond:
        fails.append(name)


text = "\n".join(f"{i}번 줄 " + "가" * 90 for i in range(400))       # 약 4만 자
parts = D.split_parts(text, 8000)
check("긴 문서는 여러 부분으로 나뉜다", len(parts) >= 5, len(parts))
check("각 부분은 한도(8,000자) 이내", all(len(p) <= 8000 for p in parts), max(len(p) for p in parts))
check("이어 붙이면 원문과 같다(내용 손실 없음)", "\n".join(parts) == text)
check("줄 중간에서 자르지 않는다", all(not p.startswith("가") for p in parts))
check("짧은 문서는 한 부분", D.split_parts("짧은 문서\n두 줄", 8000) == ["짧은 문서\n두 줄"])
check("한 줄이 한도보다 길면 강제로 자른다", [len(p) for p in D.split_parts("나" * 20000, 8000)] == [8000, 8000, 4000])
t, cur, total = D.pick_part(text, 2, 8000)
check("pick_part: 번호와 전체 수", cur == 2 and total == len(parts) and t == parts[1])
check("pick_part: 기본은 1부", D.pick_part(text, None, 8000)[1] == 1)
for bad in (0, 99):
    try:
        D.pick_part(text, bad, 8000)
        check(f"범위를 벗어난 part={bad}는 오류", False)
    except ValueError:
        check(f"범위를 벗어난 part={bad}는 오류", True)
check("빈 문서도 한 부분", D.split_parts("") == [""])
print("\n전부 통과" if not fails else f"\n실패 {len(fails)}건: {fails}")
sys.exit(1 if fails else 0)
