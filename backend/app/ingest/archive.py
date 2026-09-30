"""ZIP 업로드 안전 처리.

원칙: 압축 항목 이름으로는 파일을 만들지 않는다(저장은 내용 해시 경로). 이름은 표시용 메타데이터일 뿐이다.
그래도 경로 조작·중첩 압축·암호화·압축 폭탄·심볼릭 링크는 사유를 남기고 항목 단위로 거부한다.
헤더에 적힌 크기를 믿지 않고 실제로 읽은 양으로 한도를 강제한다(store_stream).
"""
import posixpath
import stat
import zipfile
from dataclasses import dataclass

from .. import config

JUNK_NAMES = {".ds_store", "thumbs.db", "desktop.ini"}


class ArchiveRejected(Exception):
    """ZIP 전체를 처리할 수 없을 때(손상·항목 과다·총용량 초과)"""


@dataclass
class Entry:
    name: str                 # 표시용 이름(ZIP 내부 경로)
    info: zipfile.ZipInfo | None
    reject: str | None = None
    skip: bool = False        # 조용히 건너뜀(디렉터리·OS 메타데이터)


def decode_name(info: zipfile.ZipInfo) -> str:
    """UTF-8 플래그가 없는 ZIP(윈도우 탐색기 등)의 한글 이름이 깨지는 문제를 보정한다(cp437로 읽힌 cp949)."""
    name = info.filename
    if not (info.flag_bits & 0x800):
        try:
            name = name.encode("cp437").decode("cp949")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    return name


def _bad_path(name: str) -> bool:
    n = name.replace("\\", "/")
    if n.startswith("/") or (len(n) > 1 and n[1] == ":") or "\x00" in n:
        return True
    return any(p == ".." for p in n.split("/"))


def classify(fileobj) -> tuple[zipfile.ZipFile, list[Entry]]:
    """ZIP을 열어 항목별 판정(허용/거부/건너뜀)을 만든다. 실제 읽기는 호출자가 한다."""
    try:
        zf = zipfile.ZipFile(fileobj)
    except zipfile.BadZipFile:
        raise ArchiveRejected("손상되었거나 ZIP 형식이 아닌 파일입니다.")
    infos = zf.infolist()
    if len(infos) > config.MAX_ZIP_ENTRIES:
        raise ArchiveRejected(f"압축 파일 안의 항목이 너무 많습니다({len(infos)}개 > {config.MAX_ZIP_ENTRIES}개).")
    declared = sum(i.file_size for i in infos)
    if declared > config.MAX_ZIP_UNCOMPRESSED_MB * 1024 * 1024:
        raise ArchiveRejected(f"압축을 풀면 용량이 한도({config.MAX_ZIP_UNCOMPRESSED_MB}MB)를 넘습니다.")
    out: list[Entry] = []
    for info in infos:
        name = decode_name(info)
        base = posixpath.basename(name.replace("\\", "/"))
        if info.is_dir() or name.endswith("/"):
            out.append(Entry(name, info, skip=True)); continue
        if name.startswith("__MACOSX/") or base.lower() in JUNK_NAMES or base.startswith("._"):
            out.append(Entry(name, info, skip=True)); continue
        if _bad_path(name):
            out.append(Entry(name, info, reject="허용되지 않는 경로입니다(상위 경로 또는 절대 경로)")); continue
        if info.flag_bits & 0x1:
            out.append(Entry(name, info, reject="암호로 보호된 압축 항목입니다")); continue
        if stat.S_ISLNK(info.external_attr >> 16):
            out.append(Entry(name, info, reject="심볼릭 링크는 허용되지 않습니다")); continue
        ext = posixpath.splitext(base)[1].lower()
        if ext in config.ARCHIVE_EXT:
            out.append(Entry(name, info, reject="압축 파일 안의 압축 파일(중첩 압축)은 지원하지 않습니다")); continue
        if ext not in config.ALLOWED_EXT:
            out.append(Entry(name, info, reject=f"지원하지 않는 형식입니다({ext or '확장자 없음'})")); continue
        if info.file_size > config.MAX_FILE_MB * 1024 * 1024:
            out.append(Entry(name, info, reject=f"파일이 너무 큽니다(최대 {config.MAX_FILE_MB}MB)")); continue
        if (info.compress_size > 0 and info.file_size > 10 * 1024 * 1024
                and info.file_size / info.compress_size > config.MAX_ZIP_RATIO):
            out.append(Entry(name, info, reject="압축률이 비정상적으로 높아 거부했습니다")); continue
        out.append(Entry(name, info))
    return zf, out
