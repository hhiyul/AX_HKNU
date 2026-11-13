"""Convert the stored Markdown revision; never call a model or auto-save edits."""
from html.parser import HTMLParser
import os
import shutil
import subprocess
from tempfile import TemporaryDirectory
from pathlib import Path

import markdown


class ExportError(Exception):
    """A sanitized converter failure suitable for a user-facing response."""


def _converter(setting: str, name: str) -> str:
    configured = os.environ.get(setting)
    binary = configured or (shutil.which("soffice.com") if name == "soffice" and os.name == "nt" else None) or shutil.which(name)
    if not binary or (configured and not Path(configured).is_file()):
        raise ExportError(f"{name.upper()}_UNAVAILABLE")
    if name == "soffice" and os.name == "nt" and Path(binary).suffix.lower() == ".exe":
        console = Path(binary).with_suffix(".com")
        if console.is_file():
            binary = str(console)
    return str(binary)


def _run(args: list[str], *, input_bytes: bytes | None = None) -> None:
    try:
        result = subprocess.run(args, input=input_bytes, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=90, check=False)
    except subprocess.TimeoutExpired as exc:
        raise ExportError("CONVERSION_TIMEOUT") from exc
    except OSError as exc:
        raise ExportError("CONVERTER_UNAVAILABLE") from exc
    if result.returncode:
        raise ExportError("CONVERSION_FAILED")


def _office_export(body: str, fmt: str) -> bytes:
    pandoc = _converter("P5_PANDOC_PATH", "pandoc")
    soffice = _converter("P5_SOFFICE_PATH", "soffice") if fmt == "pdf" else None
    with TemporaryDirectory(prefix="p5-export-") as directory:
        root = Path(directory)
        docx = root / "document.docx"
        _run([pandoc, "--sandbox", "--from=markdown+pipe_tables", "--to=docx", "--output", str(docx)], input_bytes=body.encode("utf-8"))
        if not docx.is_file() or not docx.stat().st_size:
            raise ExportError("CONVERSION_FAILED")
        if fmt == "docx":
            return docx.read_bytes()
        profile = (root / "libreoffice-profile").as_uri()
        _run([soffice, f"-env:UserInstallation={profile}", "--headless", "--convert-to", "pdf:writer_pdf_Export", "--outdir", str(root), str(docx)])
        pdf = root / "document.pdf"
        if not pdf.is_file() or not pdf.stat().st_size:
            raise ExportError("CONVERSION_FAILED")
        return pdf.read_bytes()


class TextBlocks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.blocks = []
        self.current = []
        self.kind = "p"
        self.href = None

    def handle_starttag(self, tag, attrs):
        if tag in ("p", "li", "h1", "h2", "h3", "pre", "blockquote"):
            self.flush()
            self.current, self.kind = ([('• ', None)] if tag == "li" else []), tag
        elif tag == "a":
            self.href = dict(attrs).get("href")
        elif tag == "br":
            self.current.append(("\n", None))

    def handle_data(self, data):
        self.current.append((data, self.href))

    def handle_endtag(self, tag):
        if tag == "a":
            self.href = None
        if tag in ("p", "li", "h1", "h2", "h3", "pre", "blockquote"):
            self.flush()

    def flush(self):
        if any(text.strip() for text, _ in self.current):
            self.blocks.append((self.kind, self.current))
        self.current = []


def _plain(parts):
    return "".join(text + (" (" + href + ")" if href else "") for text, href in parts).strip()


def export(body: str, fmt: str) -> tuple[bytes, str]:
    if fmt == "md":
        return body.encode("utf-8"), "text/markdown; charset=utf-8"
    if fmt == "txt":
        parser = TextBlocks()
        parser.feed(markdown.markdown(body))
        parser.flush()
        return "\n\n".join(_plain(parts) for _, parts in parser.blocks).encode("utf-8"), "text/plain; charset=utf-8"
    if fmt == "docx":
        return _office_export(body, fmt), "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    if fmt == "pdf":
        return _office_export(body, fmt), "application/pdf"
    raise ValueError("UNSUPPORTED_FORMAT")
