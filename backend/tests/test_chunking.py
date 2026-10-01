from backend.app.ingest import chunking, metadata

BRIEF = """준 비 서 면

사 건 2026가합90001 대여금
원 고 테스트갑
피 고 테스트을

1. 상대방 주장의 요지
원고는 대여금 전액의 지급을 구합니다.

2. 인정 및 부인
가. 대여 사실은 인정합니다.

3. 쟁점별 반박
가. 소멸시효의 완성
원고 채권은 시효로 소멸하였습니다. 원고 채권은 시효로 소멸하였습니다.
나. 승인 주장에 대한 반박
문자메시지는 승인에 해당하지 않습니다.

4. 입증방법
을 제1호증 문자메시지

5. 결론
원고의 청구는 기각되어야 합니다."""


def test_brief_structure_chunks():
    ch = chunking.chunk_text(BRIEF)
    labels = [l for l, _ in ch]
    assert labels[0] == "표제"
    assert "1. 상대방 주장의 요지" in labels and "5. 결론" in labels
    assert "3. 쟁점별 반박 > 가." in labels and "3. 쟁점별 반박 > 나." in labels
    assert "3. 쟁점별 반박" not in labels                      # 제목 줄만 있는 조각은 만들지 않는다
    # 섹션 표시는 고정된 구조 이름일 뿐 본문에서 가져오지 않는다
    assert all("소멸시효" not in l and "테스트" not in l for l in labels)


def test_brief_structure_survives_ocr_spacing_loss():
    squeezed = BRIEF.replace("1. 상대방 주장의 요지", "1.상대방주장의요지").replace("5. 결론", "5.결론").replace("4. 입증방법", "## 4. 입증방법")
    labels = [l for l, _ in chunking.chunk_text(squeezed)]
    assert "1. 상대방 주장의 요지" in labels and "4. 입증방법" in labels and "5. 결론" in labels


def test_numbered_claims_in_a_complaint_are_not_headings():
    doc = "소 장\n\n1. 피고는 원고에게 금 1,000만 원을 지급하라.\n\n2. 피고는 관리소장 홍길동이 리베이트를 받았다는 글을 게시하였습니다.\n"
    labels = [l for l, _ in chunking.chunk_text(doc)]
    assert all(l.startswith("본문") for l in labels)             # 번호 문장이 섹션명이 되어 본문이 새는 일이 없다
    assert all("홍길동" not in l for l in labels)


def test_long_document_is_split_into_bounded_chunks():
    para = "이것은 계약서의 한 조항입니다. " * 200
    ch = chunking.chunk_text("\n\n".join([para] * 3))
    assert len(ch) >= 6 and all(len(t) <= chunking.MAX_CHARS + 100 for _, t in ch)


def test_metadata_detection():
    d = metadata.detect(BRIEF + "\n\n2026. 3. 10.\n피고 소송대리인 변호사 홍길동")
    assert d["doc_kind"] == "준 비 서 면" and d["case_no"] == "2026가합90001" and d["author_side"] == "피고"
    assert d["filed_date"] == "2026-03-10"
    assert metadata.detect("답 변 서\n사 건 2025가단11111 대여금")["doc_kind"] == "답 변 서"
    multi = metadata.detect("준비서면\n2025가합11111 관련 2024가합22222 사건")
    assert "case_no" not in multi and len(multi["case_no_candidates"]) == 2      # 여러 개면 자동 지정하지 않는다


def test_text_quality_gate():
    assert not metadata.text_quality("")[0]
    assert not metadata.text_quality("a b")[0]
    assert not metadata.text_quality("!@#$%^&*()" * 30)[0]
    assert metadata.text_quality("원고는 2023. 3. 15. 피고에게 금원을 대여하였습니다. " * 5)[0]


def test_filed_date_needs_a_signature_block():
    """제출일은 서명란(귀중·소송대리인) 앞 날짜만 인정한다. 본문 중간의 사실관계 날짜를 제출일로 오인하지 않는다."""
    body = "준 비 서 면\n사 건 2026가합90001 대여금\n" + "원고는 2023. 3. 15. 피고에게 금원을 대여하였다고 주장합니다. " * 3
    assert "filed_date" not in metadata.detect(body)                                          # 서명란이 없는 문서
    assert "filed_date" not in metadata.detect(body + "\n나. 2.\n피고 소송대리인")               # 서명란 날짜가 손상된 문서: 대여일을 제출일로 쓰지 않는다
    ok = metadata.detect(body + "\n2026. 5. 20.\n피고 소송대리인 변호사 홍길동\n서울중앙지방법원 귀중")
    assert ok["filed_date"] == "2026-05-20"
    assert metadata.detect(body + "\n2026. 5. 20.\n서울중앙지방법원 제12민사부 귀중")["filed_date"] == "2026-05-20"


def test_doc_kind_comes_from_the_title_line_only():
    """본문에 '도급계약서' 같은 단어가 있어도 제목이 '증거설명서'면 계약서로 분류하지 않는다."""
    d = metadata.detect("증 거 설 명 서\n사 건 2025가합31017 부당이득금\n을 제2호증 도급계약서: 2025. 2. 3. 체결")
    assert "doc_kind" not in d
    assert metadata.detect("계 약 서\n제1조 (목적)")["doc_kind"] == "계약서"
    assert metadata.detect("\n\n  준 비 서 면 (제2차)\n사 건 2025가합1 대여금")["doc_kind"] == "준 비 서 면"
    assert metadata.detect("준비서면\n의견서를 첨부합니다")["doc_kind"] == "준 비 서 면"      # 본문의 다른 종류 단어에 흔들리지 않는다


def test_structured_brief_with_long_paragraphs_stays_within_embedding_limit():
    """긴 문단이 있는 서면(20쪽 규모)에서도 어떤 청크도 SPLIT_LIMIT을 넘지 않는다.
    (한도 1,200자일 때 1,197자 청크가 662토큰이 되어 임베딩 입력 한도 512토큰을 넘어 뒤가 잘렸던 문제의 회귀 방지)"""
    long_para = "원고는 이 사건 대여금에 관하여 시효가 중단되었다고 주장하나 이는 사실과 다릅니다. " * 14   # 약 1,100자, 줄바꿈 없음
    text = BRIEF.replace("원고 채권은 시효로 소멸하였습니다. 원고 채권은 시효로 소멸하였습니다.", long_para)
    text = text.replace("문자메시지는 승인에 해당하지 않습니다.", long_para + "\n" + long_para)
    ch = chunking.chunk_text(text)
    assert max(len(t) for _, t in ch) <= chunking.SPLIT_LIMIT
    labels = [l for l, _ in ch]
    assert any(l.startswith("3.") for l in labels) and any(l.startswith("5.") for l in labels)


def test_twenty_page_brief_is_chunked_with_all_standard_sections():
    body = ("\n".join(f"{'가나다라마바사아자차'[i % 10]}. 쟁점 {i + 1}\n" + ("이 쟁점에 관한 피고의 주장은 다음과 같습니다. " * 30) for i in range(20)))
    text = BRIEF.split("3. 쟁점별 반박")[0] + "3. 쟁점별 반박\n" + body + "\n\n4. 입증방법\n을 제1호증\n\n5. 결론\n기각되어야 합니다."
    assert len(text) > 15000
    ch = chunking.chunk_text(text)
    labels = {l.split(" > ")[0].split(" (")[0] for l, _ in ch}
    assert {"1. 상대방 주장의 요지", "2. 인정 및 부인", "3. 쟁점별 반박", "4. 입증방법", "5. 결론"} <= labels
    assert max(len(t) for _, t in ch) <= chunking.SPLIT_LIMIT


# ---- 스캔 문서의 쪽 단위 품질 검사

def test_suspect_pages_flags_thin_ocr_pages_that_the_document_level_gate_misses():
    pages = [816, 883, 9, 832, 801, 112, 46, 814, 829, 816]            # 20% 가까운 쪽이 거의 비었지만 문서 전체 글자 수는 충분
    assert metadata.text_quality("가" * sum(pages))[0] is True            # 문서 단위 검사는 통과한다
    assert metadata.suspect_pages(pages, ocr_pages=list(range(1, 11))) == [3, 6, 7]


def test_suspect_pages_ignores_natural_short_last_page_unless_ocr_warned():
    pages = [820, 840, 810, 830, 120]                                   # 마지막 쪽(결론·서명)은 원래 짧을 수 있다
    assert metadata.suspect_pages(pages, ocr_pages=[1, 2, 3, 4, 5]) == []
    assert metadata.suspect_pages(pages, ocr_pages=[1, 2, 3, 4, 5], warned_pages=[5]) == [5]


def test_suspect_pages_skips_text_layer_pages_and_tiny_documents():
    assert metadata.suspect_pages([800, 20, 800, 810], ocr_pages=[1, 3, 4]) == []   # 2쪽은 텍스트층(OCR 미적용)이라 검사 안 함
    assert metadata.suspect_pages([30, 10, 20], ocr_pages=[1, 2, 3]) == []          # 기준이 너무 작으면 비교하지 않는다
    assert metadata.suspect_pages([], ocr_pages=[]) == []


def test_two_page_scan_with_one_empty_page_is_flagged():
    assert metadata.suspect_pages([850, 40], ocr_pages=[1, 2], warned_pages=[2]) == [2]
    assert metadata.suspect_pages([40, 850], ocr_pages=[1, 2]) == [1]


def test_page_report_reads_kordoc_json_fields():
    data = {"pages": [{"markdown": "가" * 600 + "\n![image](image_001.png)"}, {"markdown": "나" * 5}, {"markdown": "다" * 590}, {"markdown": "라" * 610}],
            "pageQuality": [{"page": i, "ocrApplied": True} for i in (1, 2, 3, 4)],
            "warnings": [{"page": 2, "code": "OCR_LOW_CONF", "message": "페이지 2: 저신뢰 OCR 라인 7개 폐기 (인식 결손 가능)"}]}
    rep = metadata.page_report(data)
    assert rep["pages"] == 4 and rep["page_chars"] == [600, 5, 590, 610]           # 이미지 자리 표시는 글자 수에서 제외
    assert rep["suspect_pages"] == [2] and rep["ocr_discarded_lines"] == 7
