-- 006: 사건별 관련자(제3자) 이름 목록
-- 마스킹이 당사자·담당 변호사 외에 이 목록의 이름도 가리게 한다(사건의 증인·대표이사·상대방 직원 등).
-- 이름을 NER로 추측하지 않고 사건 담당자가 등록한 값만 치환한다.
ALTER TABLE matters ADD COLUMN related_names text[] NOT NULL DEFAULT '{}';
