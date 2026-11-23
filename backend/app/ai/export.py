"""Markdown 글을 내려받을 파일로 바꾼다. 조원 파이프라인의 내보내기(P5 export.py)를 옮겼다.

DOCX는 Pandoc으로, PDF는 그 DOCX를 LibreOffice로 바꾼다. 둘 다 서버 이미지(backend/Dockerfile)에 들어 있다.
다른 위치의 실행 파일을 쓰려면 PANDOC_PATH, SOFFICE_PATH 환경 변수에 절대 경로를 넣는다.
"""
import os
import shutil
import subprocess
import threading
from html.parser import HTMLParser
from pathlib import Path
from tempfile import TemporaryDirectory

import markdown

DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
FORMATS = {
    "md": "text/markdown; charset=utf-8",
    "txt": "text/plain; charset=utf-8",
    "docx": DOCX_TYPE,
    "pdf": "application/pdf",
}
CONVERT_TIMEOUT_SECONDS = 90
# 원시 HTML(<img> 등)은 받지 않는다.
PANDOC_INPUT = "--from=markdown+pipe_tables-raw_html-raw_attribute"
# Pandoc이 읽은 문서에서 이미지를 글(대체 글)로 바꾼다. 이미지가 없으면 Pandoc은 서버 파일이나 외부 주소를 읽지 않는다.
# Debian의 Pandoc 2.17은 --sandbox를 켜면 자기 데이터 파일도 못 읽어서 이 필터로 대신한다.
NO_IMAGES_FILTER = "function Image(el) return el.caption end\n"
# LibreOffice는 변환 하나에 수백 MB를 쓴다. 동시에 여러 명이 내보내도 2개씩만 변환한다.
OFFICE_SLOTS = threading.BoundedSemaphore(2)


class ExportError(Exception):
    """변환 도구가 없거나 변환에 실패했다. 메시지는 사용자에게 보여 줄 수 있는 코드다."""


def _converter(setting: str, name: str) -> str:
    configured = os.environ.get(setting)
    binary = configured or shutil.which(name)
    if not binary or (configured and not Path(configured).is_file()):
        raise ExportError(f"{name.upper()}_UNAVAILABLE")
    return str(binary)


def _run(args: list[str], input_bytes: bytes | None = None, cwd: Path | None = None) -> None:
    """빈 임시 폴더에서 최소한의 환경 변수로 돌린다(상대 경로나 환경 변수로 서버 정보를 읽지 못하게)."""
    env = {"PATH": os.environ.get("PATH", ""), "LANG": "C.UTF-8", "HOME": str(cwd) if cwd else "/tmp"}
    try:
        result = subprocess.run(
            args, input=input_bytes, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=CONVERT_TIMEOUT_SECONDS, check=False, cwd=cwd, env=env,
        )
    except subprocess.TimeoutExpired as exc:
        raise ExportError("CONVERSION_TIMEOUT") from exc
    except OSError as exc:
        raise ExportError("CONVERTER_UNAVAILABLE") from exc
    if result.returncode:
        raise ExportError("CONVERSION_FAILED")


def _office_export(body: str, fmt: str) -> bytes:
    """요청마다 임시 폴더와 LibreOffice 프로필을 따로 써서 동시에 변환해도 섞이지 않게 한다."""
    pandoc = _converter("PANDOC_PATH", "pandoc")
    soffice = _converter("SOFFICE_PATH", "soffice") if fmt == "pdf" else None
    with OFFICE_SLOTS, TemporaryDirectory(prefix="ax-export-") as directory:
        root = Path(directory)
        docx = root / "document.docx"
        lua = root / "no-images.lua"
        lua.write_text(NO_IMAGES_FILTER, encoding="utf-8")
        _run(
            [pandoc, PANDOC_INPUT, f"--lua-filter={lua}", "--to=docx", "--output", str(docx)],
            body.encode("utf-8"), cwd=root,
        )
        if not docx.is_file() or not docx.stat().st_size:
            raise ExportError("CONVERSION_FAILED")
        if fmt == "docx":
            return docx.read_bytes()
        profile = (root / "libreoffice-profile").as_uri()
        _run([soffice, f"-env:UserInstallation={profile}", "--headless", "--convert-to", "pdf:writer_pdf_Export",
              "--outdir", str(root), str(docx)], cwd=root)
        pdf = root / "document.pdf"
        if not pdf.is_file() or not pdf.stat().st_size:
            raise ExportError("CONVERSION_FAILED")
        return pdf.read_bytes()


BLOCK_TAGS = ("p", "li", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "blockquote")
# 문서에 섞인 HTML의 스크립트·스타일 내용은 글로 내보내지 않는다.
HIDDEN_TAGS = ("script", "style")


class _TextBlocks(HTMLParser):
    """Markdown을 HTML로 바꾼 뒤 문단·목록·제목 단위의 글로 모은다. 링크는 "글 (주소)"로 남긴다."""

    def __init__(self):
        super().__init__()
        self.blocks: list[list[tuple[str, str | None]]] = []
        self.current: list[tuple[str, str | None]] = []
        self.href: str | None = None
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in HIDDEN_TAGS:
            self.hidden += 1
        elif tag in BLOCK_TAGS:
            self.flush()
            self.current = [("• ", None)] if tag == "li" else []
        elif tag == "a":
            self.href = dict(attrs).get("href")
        elif tag == "br":
            self.current.append(("\n", None))

    def handle_data(self, data):
        if not self.hidden:
            self.current.append((data, self.href))

    def handle_endtag(self, tag):
        if tag in HIDDEN_TAGS:
            self.hidden = max(0, self.hidden - 1)
            return
        if tag == "a":
            self.href = None
        if tag in BLOCK_TAGS:
            self.flush()

    def flush(self):
        if any(text.strip() for text, _ in self.current):
            self.blocks.append(self.current)
        self.current = []


def _plain(parts: list[tuple[str, str | None]]) -> str:
    """같은 링크의 글 조각을 모아 "글 (주소)" 한 번으로 쓴다."""
    out, previous = [], None
    for text, href in parts:
        if previous and href != previous:
            out.append(f" ({previous})")
        out.append(text)
        previous = href
    if previous:
        out.append(f" ({previous})")
    return "".join(out).strip()


def to_text(body: str) -> str:
    parser = _TextBlocks()
    parser.feed(markdown.markdown(body))
    parser.flush()
    return "\n\n".join(_plain(parts) for parts in parser.blocks)


def export(body: str, fmt: str) -> tuple[bytes, str]:
    """(파일 내용, Content-Type). 모르는 형식이면 ValueError."""
    if fmt == "md":
        return body.encode("utf-8"), FORMATS["md"]
    if fmt == "txt":
        return to_text(body).encode("utf-8"), FORMATS["txt"]
    if fmt in ("docx", "pdf"):
        return _office_export(body, fmt), FORMATS[fmt]
    raise ValueError("UNSUPPORTED_FORMAT")
