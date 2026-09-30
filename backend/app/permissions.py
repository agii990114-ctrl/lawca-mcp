"""역할별 권한표. 사건·문서 열람 범위는 역할이 아니라 접근 그룹(DB의 RLS)이 정한다.

관리자는 사용자·그룹을 관리하지만 사건 내용은 자신이 속한 접근 그룹 안에서만 볼 수 있다.
(이해충돌 방지벽을 시스템 관리자가 우회하지 못하게 하기 위함)
"""

PERMISSIONS: dict[str, set[str]] = {
    "관리자": {"users.manage", "groups.manage", "audit.read",
              "matters.read", "matters.create", "documents.read", "documents.upload", "documents.delete", "jobs.manage"},
    "변호사": {"matters.read", "matters.create", "documents.read", "documents.upload", "documents.delete", "jobs.manage"},
    "사무원": {"matters.read", "matters.create", "documents.read", "documents.upload", "jobs.manage"},
}

ROLES = tuple(PERMISSIONS)


def can(role: str, permission: str) -> bool:
    return permission in PERMISSIONS.get(role, set())
