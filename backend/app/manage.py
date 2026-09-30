"""관리 CLI (소유자 계정 사용 — 서버 런타임과 분리된 운영 도구).

  python -m backend.app.manage create-admin --user-id admin --name "관리자"     # 비밀번호는 프롬프트로 입력
  python -m backend.app.manage set-password --user-id u_kim
  python -m backend.app.manage seed-dev                                          # 개발용 계정(무작위 비밀번호, .dev-credentials.txt)

비밀번호를 명령행 인자로 받지 않는다(셸 기록에 남지 않도록). 비대화형은 환경변수 LAWCA_NEW_PASSWORD.
"""
import argparse
import getpass
import os
import secrets
import sys

import psycopg

from . import config, security

DEV_USERS = [  # (user_id, 이름, 역할, 접근 그룹)
    ("u_admin", "관리자(개발)", "관리자", ["G-A", "G-B", "G-C"]),
    ("u_kim", "김서준", "변호사", ["G-A", "G-B"]),
    ("u_lee", "이하윤", "변호사", ["G-A"]),
    ("u_park", "박지안", "변호사", ["G-C"]),
    ("u_clerk", "최사무(개발)", "사무원", ["G-A", "G-B"]),
]


def _read_password(user_id: str) -> str:
    pw = os.environ.get("LAWCA_NEW_PASSWORD")
    if not pw:
        pw = getpass.getpass(f"{user_id} 비밀번호: ")
        if pw != getpass.getpass("한 번 더 입력: "):
            sys.exit("비밀번호가 일치하지 않습니다.")
    problem = security.password_problem(pw, user_id)
    if problem:
        sys.exit(problem)
    return pw


def _upsert_cred(c, user_id: str, password: str, must_change: bool):
    c.execute("INSERT INTO user_credentials (user_id, password_hash, must_change) VALUES (%s,%s,%s)"
              " ON CONFLICT (user_id) DO UPDATE SET password_hash = EXCLUDED.password_hash,"
              " must_change = EXCLUDED.must_change, updated_at = now()", (user_id, security.hash_password(password), must_change))
    c.execute("DELETE FROM sessions WHERE user_id = %s", (user_id,))


def create_admin(a):
    pw = _read_password(a.user_id)
    with psycopg.connect(config.OWNER_DSN) as c:
        if c.execute("SELECT 1 FROM users WHERE user_id = %s", (a.user_id,)).fetchone():
            sys.exit("이미 존재하는 아이디입니다.")
        c.execute("INSERT INTO users (user_id, name, role) VALUES (%s,%s,'관리자')", (a.user_id, a.name))
        _upsert_cred(c, a.user_id, pw, False)
    print(f"관리자 {a.user_id} 를 만들었습니다. (접근 그룹은 없습니다. 사건 열람이 필요하면 그룹에 추가하세요.)")


def set_password(a):
    pw = _read_password(a.user_id)
    with psycopg.connect(config.OWNER_DSN) as c:
        if not c.execute("SELECT 1 FROM users WHERE user_id = %s", (a.user_id,)).fetchone():
            sys.exit("사용자를 찾을 수 없습니다.")
        _upsert_cred(c, a.user_id, pw, False)
    print("비밀번호를 변경했습니다(기존 세션은 모두 종료).")


def seed_dev(a):
    lines = []
    with psycopg.connect(config.OWNER_DSN) as c:
        for uid, name, role, groups in DEV_USERS:
            c.execute("INSERT INTO users (user_id, name, role) VALUES (%s,%s,%s)"
                      " ON CONFLICT (user_id) DO UPDATE SET role = EXCLUDED.role, active = true", (uid, name, role))
            for g in groups:
                c.execute("INSERT INTO user_groups VALUES (%s,%s) ON CONFLICT DO NOTHING", (uid, g))
            pw = secrets.token_urlsafe(12) + "Aa1"
            _upsert_cred(c, uid, pw, False)
            lines.append(f"{uid}\t{role}\t{pw}")
    path = config.ROOT / ".dev-credentials.txt"
    path.write_text("# 개발용 계정 (git 제외). user_id<TAB>역할<TAB>비밀번호\n" + "\n".join(lines) + "\n", encoding="utf-8")
    print(f"개발용 계정 {len(DEV_USERS)}개를 준비했습니다. 비밀번호는 {path.name} 에 있습니다.")


def main():
    ap = argparse.ArgumentParser(prog="manage")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("create-admin"); p.add_argument("--user-id", required=True); p.add_argument("--name", required=True); p.set_defaults(fn=create_admin)
    p = sub.add_parser("set-password"); p.add_argument("--user-id", required=True); p.set_defaults(fn=set_password)
    p = sub.add_parser("seed-dev"); p.set_defaults(fn=seed_dev)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
