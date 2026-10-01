"""쪽별 정답과 각 엔진 출력의 문자 오류율(CER, 편집거리/정답 길이)과 속도를 비교한다. (rapidfuzz 필요)"""
import json, statistics, sys
from pathlib import Path
from rapidfuzz.distance import Levenshtein

work = Path(sys.argv[1])
eng = json.load(open(work / "engines_out.json", encoding="utf-8"))
kd = json.load(open(work / "kordoc_out.json", encoding="utf-8"))
print("| 조건 | 엔진 | 문자 오류율(CER) | 정확도 | 쪽당 시간(첫 쪽 제외) |\n|---|---|---|---|---|")
for cond in ("easy", "hard"):
    truth = json.load(open(work / f"truth_{cond}.json", encoding="utf-8"))
    for name, d in {**eng[cond], **kd[cond]}.items():
        cers = [Levenshtein.distance(t, o) / max(1, len(t)) for t, o in zip(truth, d["text"])]
        sec = d["sec"][1:] if len(d["sec"]) > 1 else d["sec"]
        print(f"| {cond} | {name} | {statistics.mean(cers):.2%} | {1-statistics.mean(cers):.1%} | {statistics.mean(sec):.1f}초 |")
