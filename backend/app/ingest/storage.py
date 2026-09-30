"""업로드 원본 저장소. 파일은 내용 해시로 저장하고(표시용 이름은 DB에만 둔다) 경로 조작 여지를 없앤다.

한계: 저장 시 암호화는 구현하지 않았다. 운영에서는 저장 볼륨 암호화(BitLocker/LUKS 등)를 전제로 한다.
"""
import hashlib
import os
import uuid
from pathlib import Path

from .. import config

BLOBS = config.STORAGE_DIR / "blobs"
TMP = config.STORAGE_DIR / "tmp"


class TooLarge(Exception):
    pass


def ensure_dirs():
    BLOBS.mkdir(parents=True, exist_ok=True)
    TMP.mkdir(parents=True, exist_ok=True)


def store_stream(read, max_bytes: int, group: str) -> tuple[str, int, str]:
    """read(n)->bytes 로 읽은 내용을 접근 그룹별 경로에 저장한다. 반환: (sha256, 크기, 저장소 상대경로).
    그룹별로 경로를 나누어, 서로 다른 그룹이 같은 파일을 공유(삭제 시 남의 원본이 지워지는 문제)하지 않게 한다.
    max_bytes를 넘으면 TooLarge (헤더 크기를 믿지 않고 실제로 읽은 양으로 제한한다)."""
    ensure_dirs()
    tmp = TMP / f"{uuid.uuid4().hex}.part"
    h, size = hashlib.sha256(), 0
    try:
        with open(tmp, "wb") as out:
            while True:
                chunk = read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise TooLarge(size)
                h.update(chunk)
                out.write(chunk)
        digest = h.hexdigest()
        rel = f"{group}/{digest[:2]}/{digest}"
        final = BLOBS / rel
        final.parent.mkdir(parents=True, exist_ok=True)
        if final.exists():
            tmp.unlink(missing_ok=True)
        else:
            os.replace(tmp, final)
        return digest, size, rel
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def blob_path(rel: str) -> Path:
    p = (BLOBS / rel).resolve()
    if BLOBS.resolve() not in p.parents:      # 방어: DB 값이 이상해도 저장소 밖은 열지 않는다
        raise ValueError("잘못된 저장 경로")
    return p


def unlink_if_orphan(conn, stored_path: str) -> bool:
    """같은 경로를 참조하는 uploaded_files 행이 더 없으면 실제 파일을 지운다(동일 내용의 다른 업로드 보호)."""
    left = conn.execute("SELECT count(*) AS n FROM uploaded_files WHERE stored_path = %s", (stored_path,)).fetchone()["n"]
    if left == 0:
        blob_path(stored_path).unlink(missing_ok=True)
        return True
    return False
