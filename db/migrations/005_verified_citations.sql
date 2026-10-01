-- 005: 사무소 확인 인용 등록부 (판례·조문)
-- 변호사가 원문을 확인해 등록한 판례·조문 목록. check_citation 도구가 초안 속 인용을 이 목록과 대조한다.
-- 공개된 법령·판례 정보이므로 사건 그룹별 접근 제한(RLS)은 두지 않는다. 민감한 사건 정보는 넣지 않는다.

CREATE TABLE verified_citations (
    kind          text NOT NULL CHECK (kind IN ('case', 'statute')),
    key           text NOT NULL,                 -- 판례: 사건번호(2004다59959), 조문: '민법 제168조'
    label         text NOT NULL,                 -- 사건명 또는 조문 제목
    summary       text,                          -- 판시 요지 또는 조문 본문(서면 내용과 대조하는 용도)
    decision_date date,                          -- 판례 선고일
    source        text NOT NULL,                 -- 어디서 확인했는지(예: 법제처 국가법령정보센터, 대법원 종합법률정보)
    verified_by   text,
    verified_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (kind, key)
);

GRANT SELECT ON verified_citations TO lawca_reader;
GRANT SELECT, INSERT, UPDATE, DELETE ON verified_citations TO lawca_app;
