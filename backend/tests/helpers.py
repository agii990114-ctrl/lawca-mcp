"""테스트 도우미: 세션 클라이언트, 테스트용 파일(ZIP·DOCX·스캔 PDF) 생성기"""
import io
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

from fastapi.testclient import TestClient

from backend.app.main import app


class Api:
    """CSRF 토큰을 자동으로 붙이는 세션 클라이언트"""
    def __init__(self):
        self.c = TestClient(app)
        self.csrf = None

    def login(self, user_id, password):
        r = self.c.post("/api/auth/login", json={"user_id": user_id, "password": password})
        if r.status_code == 200:
            self.csrf = r.json()["csrf_token"]
        return r

    def _h(self, kw):
        h = dict(kw.pop("headers", {}) or {})
        if self.csrf:
            h["X-CSRF-Token"] = self.csrf
        return dict(kw, headers=h)

    def get(self, path, **kw): return self.c.get(path, **self._h(kw))
    def post(self, path, **kw): return self.c.post(path, **self._h(kw))
    def put(self, path, **kw): return self.c.put(path, **self._h(kw))
    def patch(self, path, **kw): return self.c.patch(path, **self._h(kw))
    def delete(self, path, **kw): return self.c.delete(path, **self._h(kw))


def fake_pdf(payload: bytes = b"x" * 100) -> bytes:
    """매직 바이트만 맞춘 가짜 PDF(업로드 검증 테스트용, 내용은 유효하지 않다)"""
    return b"%PDF-1.4\n" + payload


def make_docx(paragraphs: list[str]) -> bytes:
    """최소 구조의 DOCX(문단만). 파싱 파이프라인 종단 테스트용."""
    ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    body = "".join(f'<w:p><w:r><w:t xml:space="preserve">{escape(p)}</w:t></w:r></w:p>' for p in paragraphs)
    parts = {
        "[Content_Types].xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                               '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                               '<Default Extension="xml" ContentType="application/xml"/>'
                               '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>',
        "_rels/.rels": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                       '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>',
        "word/document.xml": f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="{ns}"><w:body>{body}</w:body></w:document>',
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for k, v in parts.items():
            z.writestr(k, v)
    return buf.getvalue()


def make_zip(entries: dict[str, bytes], *, utf8_flag: bool = True) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in entries.items():
            z.writestr(name, data)
    return buf.getvalue()


def make_cp949_zip(name: str, data: bytes) -> bytes:
    """윈도우 탐색기가 만드는 ZIP처럼 UTF-8 플래그 없이 cp949 바이트로 파일명을 담는다.
    (zipfile은 한글 이름을 자동으로 UTF-8 플래그와 함께 쓰므로, 같은 길이의 ASCII 자리표시자로 만든 뒤 바이트를 치환한다.)"""
    raw = name.encode("cp949")
    placeholder = b"Q" * len(raw)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(placeholder.decode(), data)
    out = buf.getvalue().replace(placeholder, raw)
    assert out.count(raw) == 2          # 로컬 헤더 + 중앙 디렉터리
    return out


def mark_encrypted(zip_bytes: bytes, entry_name: str) -> bytes:
    """중앙 디렉터리와 로컬 헤더의 일반 목적 비트(bit 0 = 암호화)를 켠다. zipfile은 쓰기 때 이 비트를 지운다."""
    import struct
    b = bytearray(zip_bytes)
    pos, name = 0, entry_name.encode()
    while True:
        pos = b.find(b"PK", pos)
        if pos < 0:
            break
        nlen, elen, clen = struct.unpack_from("<HHH", b, pos + 28)
        local = struct.unpack_from("<I", b, pos + 42)[0]
        if bytes(b[pos + 46:pos + 46 + nlen]) == name:
            b[pos + 8] |= 0x01
            b[local + 6] |= 0x01
            return bytes(b)
        pos += 46 + nlen + elen + clen
    raise AssertionError("항목을 찾지 못했습니다")


def scan_pdf(lines: list[str]) -> bytes | None:
    """글자를 이미지로 그린 뒤 이미지뿐인 PDF로 저장한다(OCR 경로 시험). 한글 폰트가 없으면 None."""
    font = Path("C:/Windows/Fonts/batang.ttc")
    if not font.exists():
        return None
    import random
    import numpy as np
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
    img = Image.new("L", (1240, 900), 255)
    d = ImageDraw.Draw(img)
    f = ImageFont.truetype(str(font), 34)
    y = 120
    for line in lines:
        d.text((120, y), line, font=f, fill=20)
        y += 60
    img = img.rotate(1.2, fillcolor=255)
    a = np.array(img).astype(np.float32) + np.random.RandomState(3).normal(0, 10, (900, 1240))
    img = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(0.7))
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "PDF", resolution=150)
    return buf.getvalue()
