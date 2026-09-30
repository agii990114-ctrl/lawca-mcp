"""업로드 화면을 직접 써 볼 수 있는 가상 시험 문서 세트를 sample-uploads/ 에 만든다(모두 가상, 실제 인물·사건과 무관).

  backend/.venv/Scripts/python eval/make_sample_uploads.py

각 파일이 어떻게 처리돼야 하는지는 sample-uploads/README.txt 에 표로 적는다.
사건번호는 개발용 더미 DB에 등록된 사건을 쓴다(u_kim 로그인 → 접근 그룹 G-A).
"""
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.tests.helpers import make_docx, make_zip, scan_pdf  # noqa: E402

OUT = ROOT / "sample-uploads"
OUT.mkdir(exist_ok=True)
for old in OUT.iterdir():
    if old.is_file():
        old.unlink()

LOAN = dict(no="2024가단62215", type="대여금", pl="소우진", df="견하람", lawyer="변호사 나한결", side="원고", court="서울중앙지방법원 제15민사단독")
UNJUST = dict(no="2025가합31017", type="부당이득금", pl="서문하람", df="곽도윤", lawyer="변호사 봉치원", side="피고", court="서울중앙지방법원 제21민사부")
OTHER_GROUP_NO = "2024가합78846"        # G-C 전용 사건(u_kim은 볼 수 없다)


def header(kind, m, no=None, round_=None):
    title = f"{kind} (제{round_}차)" if round_ else kind
    return [title, f"사 건 {no or m['no']} {m['type']}", f"원 고 {m['pl']}", f"피 고 {m['df']}", ""]


def signature(m, date):
    return [date, f"{m['side']} 소송대리인 {m['lawyer']}", f"{m['court']} 귀중"]


def brief(m, round_, date, opponent, argue, evidence, no=None, extra=None):
    body = header("준 비 서 면", m, no, round_) + [
        "1. 상대방 주장의 요지", opponent,
        "2. 인정 및 부인", "가. 다툼 없는 사실관계는 인정합니다.", "나. 그 밖의 상대방 주장은 모두 부인합니다.",
        "3. 쟁점별 반박", f"가. {argue[0]}", argue[1], f"나. {argue[2]}", argue[3],
    ] + (extra or []) + ["4. 입증방법", evidence, "5. 결론", "따라서 상대방의 주장은 이유 없으므로 기각되어야 합니다."] + signature(m, date)
    return make_docx(body)


def photo_png(path, lines):
    """휴대폰으로 찍은 서류처럼 기울이고 노이즈·흐림을 더한 이미지"""
    import numpy as np
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
    f = ImageFont.truetype("C:/Windows/Fonts/batang.ttc", 34)
    img = Image.new("L", (1240, 980), 238)
    d = ImageDraw.Draw(img)
    y = 110
    for l in lines:
        d.text((130, y), l, font=f, fill=45)
        y += 62
    img = img.rotate(2.6, fillcolor=225, resample=Image.BICUBIC)
    a = np.array(img).astype(np.float32) + np.random.RandomState(11).normal(0, 16, (980, 1240))
    a *= np.linspace(0.93, 1.03, 1240)[None, :]                   # 한쪽이 조금 어두운 조명
    Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(1.0)).convert("RGB").save(path)


files = {}
files["01_준비서면_제2차_대여금(자동지정).docx"] = brief(
    LOAN, 2, "2026. 6. 12.",
    "피고는 2024. 1. 10. 원고에게 200만 원을 변제하였으므로 청구 금액에서 공제되어야 한다고 주장합니다.",
    ["변제 주장은 입증되지 않았습니다", "피고가 제출한 을 제3호증 이체 내역의 수취인은 원고가 아니라 원고 배우자이며 이체 메모에도 '생활비'라고 기재되어 있어 이 사건 대여금의 변제로 볼 수 없습니다.",
     "변제 충당의 순서", "설령 일부 변제가 있었다 하더라도 민법 제479조에 따라 비용, 이자, 원본의 순서로 충당되므로 원본 잔액은 그대로 남습니다."],
    "갑 제5호증 통장 거래내역, 갑 제6호증 카카오톡 대화 전문")
files["02_준비서면_제3차_부당이득금(자동지정).docx"] = brief(
    UNJUST, 3, "2026. 6. 20.",
    "원고는 피고가 수령한 공사대금 1억 2천만 원에 법률상 원인이 없어 부당이득으로 반환하여야 한다고 주장합니다.",
    ["수령에는 법률상 원인이 있습니다", "피고는 2025. 2. 3. 원고와 체결한 도급계약에 따라 옥상 방수 공사를 완료하였고 위 금원은 그 정당한 공사대금입니다.",
     "계약 무효 주장에 대하여", "원고가 주장하는 계약 무효 사유는 계약 체결 이후 원고 스스로 공사를 승인하고 준공확인서에 서명한 사실과 모순됩니다."],
    "을 제2호증 도급계약서, 을 제3호증 준공확인서")
files["03_의견서_사건번호없음(검토필요).docx"] = make_docx(
    ["의견서", "본 의견서는 상가 임대차에서 임대인이 갱신 거절 사유로 든 차임 연체가 정당한지에 관한 검토 결과입니다.", "",
     "1. 검토 배경", "임대인은 임차인이 3기의 차임을 연체하였다는 이유로 계약갱신을 거절하였고, 임차인은 연체 기간이 2기에 그친다고 다투고 있습니다. " * 3,
     "2. 결론", "상가건물 임대차보호법상 갱신 거절이 정당하려면 연체 차임이 3기의 차임액에 달하여야 하므로, 이 사안의 갱신 거절은 정당하다고 보기 어렵습니다. " * 3])
lines = ["준 비 서 면", f"사 건 {LOAN['no']} {LOAN['type']}", f"원 고 {LOAN['pl']}", f"피 고 {LOAN['df']}", "",
         "피고의 변제 주장은 증거가 없습니다.", "이체 내역의 수취인은 원고가 아닙니다.", "따라서 청구는 인용되어야 합니다.",
         "2026. 7. 3.", f"원고 소송대리인 {LOAN['lawyer']}"]
pdf = scan_pdf(lines)
if pdf:
    files["04_스캔_준비서면(OCR).pdf"] = pdf
photo_png(OUT / "05_사진풍_스캔_부당이득금(OCR).png", [
    "준 비 서 면", f"사 건 {UNJUST['no']} {UNJUST['type']}", f"원 고 {UNJUST['pl']}", f"피 고 {UNJUST['df']}", "",
    "피고가 받은 금원은 정당한 공사대금입니다.", "준공확인서에 원고가 직접 서명하였습니다.", "2026. 7. 8.", f"피고 소송대리인 {UNJUST['lawyer']}"])
files["06_제출서면_묶음(ZIP, 일부 거부).zip"] = make_zip({
    "서면/준비서면_제4차.docx": brief(LOAN, 4, "2026. 8. 1.", "피고는 이자 약정이 없었다고 주장합니다.",
                                ["이자 약정은 문자 대화로 확인됩니다", "갑 제7호증 대화에서 피고는 월 1퍼센트로 정하자는 제안에 '알겠다'고 답하였습니다.", "지연손해금", "약정이 없더라도 법정이율에 따른 지연손해금은 발생합니다."], "갑 제7호증 대화 전문"),
    "서면/증거설명서.docx": make_docx(header("증 거 설 명 서", UNJUST) + ["을 제2호증 도급계약서: 2025. 2. 3. 체결된 방수공사 도급계약", "을 제3호증 준공확인서: 원고가 서명한 준공 확인 문서", "2026. 8. 3.", f"피고 소송대리인 {UNJUST['lawyer']}"]),
    "참고/메모.txt": "지원하지 않는 형식이라 거부되어야 합니다.".encode("utf-8"),
    "참고/설치프로그램.exe": b"MZ" + b"\0" * 200,
    "__MACOSX/._준비서면.docx": b"junk",
})
files["07_복사본_01과_동일(중복).docx"] = files["01_준비서면_제2차_대여금(자동지정).docx"]
files["08_위장_텍스트를_pdf로(거부).pdf"] = "이 파일은 PDF가 아니라 그냥 텍스트입니다. 확장자만 바꿨습니다.".encode("utf-8") * 5
files["09_손상된_PDF(실패).pdf"] = b"%PDF-1.4\n" + bytes(random.Random(5).randrange(256) for _ in range(6000))
files["10_다른그룹_사건번호(검토필요).docx"] = brief(
    dict(no=OTHER_GROUP_NO, type="임대차보증금 반환", pl="가상원고", df="가상피고", lawyer="변호사 가상", side="원고", court="서울중앙지방법원 제0민사부"), 1, "2026. 5. 5.",
    "피고는 임대차 종료 후에도 보증금을 반환하지 않고 있습니다.",
    ["보증금 반환 의무", "임대차 기간이 종료되었고 목적물이 인도되었으므로 보증금을 반환하여야 합니다.", "지연이자", "종료일 다음 날부터 지연이자가 발생합니다."], "갑 제1호증 임대차계약서")
files["11_개인정보_포함_시험(가짜값).docx"] = brief(
    UNJUST, 4, "2026. 9. 1.", "원고는 피고의 계좌로 송금했다고 주장합니다.",
    ["송금 내역의 계좌는 피고 명의가 아닙니다", "송금받은 계좌번호는 110-234-567890이며 예금주는 피고 배우자입니다. 피고의 연락처는 010-5555-0101, 전자우편은 test.user@example.com입니다.",
     "본인 확인", "원고가 제출한 서류의 주민등록번호 850312-1234567은 피고의 것이 아닙니다(모두 시험용 가짜 값)."], "을 제9호증 계좌 명세")

for name, data in files.items():
    (OUT / name).write_bytes(data)

readme = f"""가상 시험 문서 세트 (모든 내용은 가상이며 실제 인물·사건과 무관합니다)

■ 사용법
1) http://localhost:8081 에서 u_kim 으로 로그인 → '업로드'
2) 접근 그룹 G-A, 사건 지정 '문서 안의 사건번호로 자동 지정', 파일은 통째로 끌어다 놓기(또는 '폴더 선택')
3) '처리 현황'에서 결과를 아래 기대 결과와 비교하세요. (스캔본은 처리에 몇 초 더 걸립니다)

■ 파일별 기대 결과
 파일                                              기대 결과
 01_준비서면_제2차_대여금(자동지정).docx            완료. 사건 {LOAN['no']}(대여금)에 자동 지정, 종류 준비서면·2차·작성 측 원고, 제출일 2026-06-12
 02_준비서면_제3차_부당이득금(자동지정).docx        완료. 사건 {UNJUST['no']}(부당이득금)에 자동 지정, 3차·작성 측 피고, 제출일 2026-06-20
 03_의견서_사건번호없음(검토필요).docx              검토 필요(사유: 사건 미지정). '검토'에서 사건을 골라 지정하면 완료
 04_스캔_준비서면(OCR).pdf                          완료(또는 OCR 오류 시 검토 필요). 텍스트 없는 스캔본을 OCR로 읽음, 사건번호 자동 지정
 05_사진풍_스캔_부당이득금(OCR).png                 완료(또는 검토 필요). 기울어진 사진풍 이미지의 OCR — 띄어쓰기는 깨져도 숫자는 정확해야 함
 06_제출서면_묶음(ZIP, 일부 거부).zip               ZIP 안 docx 2건 완료, 메모.txt·설치프로그램.exe 는 '거부됨'(사유 표시), 시스템 파일 1개는 '건너뜀'으로 집계
 07_복사본_01과_동일(중복).docx                     중복(01과 같은 내용이라 다시 처리하지 않음)
 08_위장_텍스트를_pdf로(거부).pdf                   거부됨(내용이 PDF 형식과 일치하지 않음)
 09_손상된_PDF(실패).pdf                            실패(사유 표시). '다시 시도'를 눌러도 같은 이유로 실패
 10_다른그룹_사건번호(검토필요).docx                검토 필요. 사건번호 {OTHER_GROUP_NO} 는 u_kim이 볼 수 없는 그룹의 사건이라 '일치하는 사건 없음' (격리 확인)
 11_개인정보_포함_시험(가짜값).docx                 완료. 가짜 주민번호·전화·계좌·이메일이 들어 있음(에이전트용 MCP 도구가 마스킹하는지 확인할 때 쓰는 문서)

■ 더 해 볼 것
- 같은 폴더를 한 번 더 올리면? → 앞서 처리된 파일은 전부 '중복'
- u_park 로 로그인하면 여기서 올린 문서가 하나도 보이지 않습니다(다른 접근 그룹).
- u_clerk(사무원)로 로그인하면 문서를 열어 볼 수는 있지만 '문서 삭제' 버튼이 없습니다. u_kim(변호사)에게는 있습니다.
- '문서' 메뉴에서 문서를 열면 본문과 색인 단위(1. 상대방 주장의 요지 … 5. 결론)가 보입니다.
"""
(OUT / "README.txt").write_text(readme, encoding="utf-8")
print(f"{len(files)}개 파일과 README.txt 를 {OUT} 에 만들었습니다.")
for p in sorted(OUT.iterdir()):
    print(f"  {p.name}  ({p.stat().st_size:,} bytes)")
