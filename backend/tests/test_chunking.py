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
