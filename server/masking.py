"""참고 서면을 에이전트에게 넘기기 전의 마스킹 (서버 측 수행).

- 형식이 정해진 값(주민번호·전화·이메일·사업자번호·계좌·사건번호·금액)은 정규식으로 치환한다.
- 당사자명·담당 변호사는 사건 DB의 값 목록으로 치환한다(이름을 NER로 추측하지 않는다).
한계: 본문에 나오는 제3자 인명(대표이사, 증인 등)은 탐지하지 못한다. 결과의 residual_risk 참고.
"""
import re

COURT_MARKS = "가합|가단|가소|나|다|라|마|머|드단|드합|르|카합|카단|카기|고단|고합|고정|구합|구단|누"

# 순서 중요: 더 구체적인 패턴을 먼저 적용한다.
PATTERNS = [
    ("주민등록번호", re.compile(r"\d{6}-[1-8]\d{6}"), "[주민등록번호]"),
    ("이메일", re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), "[이메일]"),
    ("사건번호", re.compile(rf"\d{{4}}(?:{COURT_MARKS})\d{{2,7}}"), "[사건번호]"),
    # \b 는 한글을 단어 문자로 보므로 "010-1234-5678입니다" 에서 경계가 잡히지 않는다 → 숫자 lookaround 사용
    ("전화번호", re.compile(r"(?<![\d-])0\d{1,2}-\d{3,4}-\d{4}(?!\d)"), "[전화번호]"),
    ("사업자등록번호", re.compile(r"(?<![\d-])\d{3}-\d{2}-\d{5}(?!\d)"), "[사업자등록번호]"),
    ("계좌번호", re.compile(r"(?<![\d-])\d{2,6}-\d{2,6}-\d{2,8}(?:-\d{1,4})?(?!\d)"), "[계좌번호]"),
    ("금액", re.compile(r"\d[\d,\.]*(?:\s*(?:조|억|천만|백만|십만|만))*\s*원(?!고|본|금|인|칙|래|장)"), "[금액]"),
]


def mask_text(text: str, *, plaintiff: str = "", defendant: str = "", attorney: str = "", case_no: str = ""):
    """(마스킹된 텍스트, 리포트) 를 돌려준다."""
    report: dict[str, int] = {}
    out = text

    def sub(label, pattern, repl):
        nonlocal out
        out, n = pattern.subn(repl, out)
        if n:
            report[label] = report.get(label, 0) + n

    # 사건 DB 값 기반 치환 (긴 이름 먼저: '주식회사 OO' 를 'OO' 보다 먼저)
    names = []
    for name, tag in ((plaintiff, "[원고]"), (defendant, "[피고]")):
        name = (name or "").strip()
        if not name:
            continue
        names.append((name, tag))
        core = re.sub(r"^(주식회사|유한회사|\(주\))\s*", "", name)
        if core and core != name:
            names.append((core, tag))
    att = re.sub(r"^변호사\s*", "", (attorney or "").strip())
    if att:
        names.append((att, "[담당변호사]"))
    for name, tag in sorted(set(names), key=lambda x: -len(x[0])):
        pat = re.compile(re.escape(name))
        out, n = pat.subn(tag, out)
        if n:
            report["당사자·변호사명"] = report.get("당사자·변호사명", 0) + n
    if case_no:
        sub("사건번호", re.compile(re.escape(case_no)), "[사건번호]")

    for label, pattern, repl in PATTERNS:
        sub(label, pattern, repl)
    return out, report


RESIDUAL_NOTE = (
    "마스킹은 형식이 정해진 값과 사건 DB의 당사자·담당 변호사 이름만 처리합니다. "
    "본문에 등장하는 제3자 인명, 상호, 주소, 구체적 사실관계(장소·일시 등)는 그대로 남아 있을 수 있으므로 "
    "새 서류에 옮기지 마세요."
)
