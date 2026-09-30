import json, re, difflib
D = json.load(open("big/data.json", encoding="utf-8"))["docs"]
ns = lambda s: re.sub(r"\s+", "", s)
tot = {"사건번호": 0, "금액": 0, "원고명": 0}; cer = []
for x in D:
    o = ns(open(f"big/ocr/{x['id']}.md", encoding="utf-8").read()); c = ns("".join(x["lines"]))
    tot["사건번호"] += ns(x["case_no"]) in o
    tot["금액"] += ns(x["amt"]) in o
    tot["원고명"] += ns(x["plaintiff"]) in o
    cer.append(1 - difflib.SequenceMatcher(None, o, c, autojunk=False).ratio())
n = len(D)
print("OCR 후 원문 그대로 남은 비율:", {k: f"{v}/{n}" for k, v in tot.items()})
print("문자 오류율(근사) 평균 %.1f%%, 최대 %.1f%%" % (100*sum(cer)/n, 100*max(cer)))
