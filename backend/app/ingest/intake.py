"""업로드 수신: 검증·저장(1단계, DB 없음) → 작업 등록(2단계, 빠른 DB 트랜잭션).

파일별 실패는 요청 전체를 실패시키지 않고 '거부(rejected)' 작업으로 기록해 사용자가 사유를 볼 수 있게 한다.
"""
import posixpath
from dataclasses import dataclass, field

from .. import config
from . import archive, storage

MAGIC = {
    ".pdf": (b"%PDF-",),
    ".hwp": (b"\xd0\xcf\x11\xe0", b"HWP Document File"),
    ".hwpx": (b"PK\x03\x04",),
    ".docx": (b"PK\x03\x04",),
    ".png": (b"\x89PNG",),
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".webp": (b"RIFF",),
}


@dataclass
class Item:
    name: str
    ext: str
    sha256: str | None = None
    size: int = 0
    stored_path: str | None = None
    reject: str | None = None


@dataclass
class Intake:
    items: list[Item] = field(default_factory=list)
    saw_zip: bool = False
    saw_plain: bool = False
    skipped: int = 0
    total_bytes: int = 0      # 일반 파일 업로드 합계(요청 용량 한도 대상)
    zip_bytes: int = 0        # ZIP 압축 해제 합계(별도 한도)


class _Head:
    """첫 바이트를 잠깐 붙잡아 시그니처(매직 바이트)를 검사한다."""
    def __init__(self, read):
        self.read, self.head = read, b""

    def __call__(self, n):
        chunk = self.read(n)
        if len(self.head) < 32:
            self.head += chunk[:32 - len(self.head)]
        return chunk


def _magic_ok(ext: str, head: bytes) -> bool:
    if ext == ".webp":
        return head[:4] == b"RIFF" and head[8:12] == b"WEBP"
    return any(head.startswith(m) for m in MAGIC.get(ext, ()))


def _store_item(name: str, read, budget: int, group: str) -> Item:
    """budget: 이 항목에 쓸 수 있는 남은 바이트(요청 한도 또는 압축 해제 총량 한도)."""
    ext = posixpath.splitext(name.replace("\\", "/"))[1].lower()
    limit = config.MAX_FILE_MB * 1024 * 1024
    if budget <= 0:
        return Item(name, ext, reject="용량 한도를 넘었습니다")
    head = _Head(read)
    try:
        sha, size, rel = storage.store_stream(head, min(limit, budget), group)
    except storage.TooLarge:
        return Item(name, ext, reject=f"파일이 너무 큽니다(최대 {config.MAX_FILE_MB}MB) 또는 용량 한도 초과")
    if size == 0:
        return Item(name, ext, sha256=sha, size=0, stored_path=rel, reject="빈 파일입니다")
    if not _magic_ok(ext, head.head):
        # 내용이 확장자와 다르다(위장 파일). 저장된 파일은 2단계에서 참조가 없으면 정리한다.
        return Item(name, ext, sha256=sha, size=size, stored_path=rel, reject=f"파일 내용이 {ext} 형식과 일치하지 않습니다")
    return Item(name, ext, sha256=sha, size=size, stored_path=rel)


def receive(files: list[tuple[str, object]], group: str) -> Intake:
    """files: [(업로드된 파일명, 파일 객체)]. 디스크에 저장하고 항목별 판정을 돌려준다."""
    intake = Intake()
    request_budget = config.MAX_REQUEST_MB * 1024 * 1024
    zip_budget = config.MAX_ZIP_UNCOMPRESSED_MB * 1024 * 1024
    if len(files) > config.MAX_FILES_PER_REQUEST:
        raise ValueError(f"한 번에 올릴 수 있는 파일은 {config.MAX_FILES_PER_REQUEST}개까지입니다. 나누어 올려 주세요.")
    for filename, fobj in files:
        base = posixpath.basename(filename.replace("\\", "/")) or "이름없음"
        ext = posixpath.splitext(base)[1].lower()
        if ext == ".zip":
            intake.saw_zip = True
            try:
                zf, entries = archive.classify(fobj)
            except archive.ArchiveRejected as e:
                intake.items.append(Item(base, ext, reject=str(e)))
                continue
            with zf:
                for e in entries:
                    label = f"{base}/{e.name}"
                    if e.skip:
                        intake.skipped += 1
                    elif e.reject:
                        intake.items.append(Item(label, posixpath.splitext(e.name)[1].lower(), reject=e.reject))
                    else:
                        with zf.open(e.info) as src:
                            it = _store_item(label, src.read, zip_budget - intake.zip_bytes, group)
                        intake.zip_bytes += it.size
                        intake.items.append(it)
            continue
        intake.saw_plain = True
        if ext in config.ARCHIVE_EXT:
            intake.items.append(Item(base, ext, reject="ZIP 이외의 압축 형식은 지원하지 않습니다"))
        elif ext not in config.ALLOWED_EXT:
            intake.items.append(Item(base, ext, reject=f"지원하지 않는 형식입니다({ext or '확장자 없음'})"))
        else:
            it = _store_item(base, fobj.read, request_budget - intake.total_bytes, group)
            intake.total_bytes += it.size
            intake.items.append(it)
    return intake
