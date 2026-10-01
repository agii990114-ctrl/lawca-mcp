"""확인 인용 등록부에 개발용 조문을 넣는다(sample-case/04_확인된_조문.md 와 같은 4개).

사용: python db/seed_citations.py [--file extra.json]
extra.json 형식: [{"kind": "case", "key": "2004다59959", "label": "...", "summary": "...", "decision_date": "2005-02-17",
                   "source": "...", "verified_by": "..."}]
운영에서는 변호사가 원문을 직접 확인한 항목만 등록한다. 이 스크립트의 기본 항목은 개발·시험용이다.
"""
import argparse
import json
import os
import sys
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parent.parent
_envfile = ROOT / ".env"
env = dict(l.strip().split("=", 1) for l in _envfile.read_text().splitlines() if "=" in l) if _envfile.exists() else {}
env.update({k: v for k, v in os.environ.items() if k.startswith("LAWCA_")})
DSN = (f"host={env.get('LAWCA_DB_HOST', '127.0.0.1')} port={env['LAWCA_DB_PORT']} dbname={env['LAWCA_DB_NAME']} "
       f"user=lawca_owner password={env['LAWCA_OWNER_PASSWORD']}")

DEV = [
    ("statute", "민법 제162조", "채권, 재산권의 소멸시효", "제1항 채권은 10년간 행사하지 아니하면 소멸시효가 완성한다."),
    ("statute", "민법 제166조", "소멸시효의 기산점", "제1항 소멸시효는 권리를 행사할 수 있는 때로부터 진행한다."),
    ("statute", "민법 제168조", "소멸시효의 중단사유", "소멸시효는 다음 각 호의 사유로 인하여 중단된다. 1. 청구 2. 압류 또는 가압류, 가처분 3. 승인"),
    ("statute", "민사소송법 제202조", "자유심증주의",
     "법원은 변론 전체의 취지와 증거조사의 결과를 참작하여 자유로운 심증으로 사실주장이 진실한지 아닌지를 판단한다."),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file")
    a = ap.parse_args()
    rows = [dict(kind=k, key=key, label=lab, summary=s, decision_date=None,
                 source="개발용 시드(sample-case/04_확인된_조문.md)", verified_by="dev-seed") for k, key, lab, s in DEV]
    if a.file:
        rows += json.loads(Path(a.file).read_text(encoding="utf-8"))
    with psycopg.connect(DSN, autocommit=True) as c:
        for r in rows:
            c.execute(
                "INSERT INTO verified_citations (kind, key, label, summary, decision_date, source, verified_by)"
                " VALUES (%(kind)s,%(key)s,%(label)s,%(summary)s,%(decision_date)s,%(source)s,%(verified_by)s)"
                " ON CONFLICT (kind, key) DO UPDATE SET label=EXCLUDED.label, summary=EXCLUDED.summary,"
                " decision_date=EXCLUDED.decision_date, source=EXCLUDED.source, verified_by=EXCLUDED.verified_by, verified_at=now()", r)
    print(f"등록 {len(rows)}건")


if __name__ == "__main__":
    sys.exit(main())
