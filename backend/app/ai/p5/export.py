"""Convert the stored Markdown revision; never call a model or auto-save edits."""
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

import markdown
from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.opc.constants import RELATIONSHIP_TYPE
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer


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


def _hyperlink(paragraph, text, href):
    relation = paragraph.part.relate_to(href, RELATIONSHIP_TYPE.HYPERLINK, is_external=True)
    link = OxmlElement("w:hyperlink")
    link.set(qn("r:id"), relation)
    run = OxmlElement("w:r")
    node = OxmlElement("w:t")
    node.text = text
    run.append(node)
    link.append(run)
    paragraph._p.append(link)


def export(body: str, fmt: str) -> tuple[bytes, str]:
    if fmt == "md":
        return body.encode("utf-8"), "text/markdown; charset=utf-8"
    parser = TextBlocks()
    parser.feed(markdown.markdown(body))
    parser.flush()
    if fmt == "txt":
        return "\n\n".join(_plain(parts) for _, parts in parser.blocks).encode("utf-8"), "text/plain; charset=utf-8"
    if fmt == "docx":
        doc = Document()
        for tag, parts in parser.blocks:
            if tag in ("h1", "h2", "h3"):
                paragraph = doc.add_heading(level=int(tag[1]))
            else:
                paragraph = doc.add_paragraph()
            for text, href in parts:
                if href and href.startswith(("https://", "http://")):
                    _hyperlink(paragraph, text, href)
                else:
                    paragraph.add_run(text + (" (" + href + ")" if href else ""))
        out = BytesIO()
        doc.save(out)
        return out.getvalue(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    if fmt == "pdf":
        font_candidates = [Path("C:/Windows/Fonts/malgun.ttf"), Path("/usr/share/fonts/truetype/nanum/NanumGothic.ttf")]
        font = next((p for p in font_candidates if p.exists()), None)
        if not font:
            raise ValueError("KOREAN_FONT_UNAVAILABLE")
        pdfmetrics.registerFont(TTFont("P5Korean", str(font)))
        normal = ParagraphStyle("P5", fontName="P5Korean", fontSize=10, leading=16, textColor=colors.black, wordWrap="CJK")
        title = ParagraphStyle("P5Title", parent=normal, fontSize=15, leading=22)
        out = BytesIO()
        flow = []
        for tag, parts in parser.blocks:
            runs = []
            for text, href in parts:
                fragment = escape(text).replace("\n", "<br/>")
                if href and href.startswith(("https://", "http://")):
                    fragment = "<link href=" + quoteattr(href) + ">" + fragment + "</link>"
                elif href:
                    fragment += " (" + escape(href) + ")"
                runs.append(fragment)
            flow.append(Paragraph("".join(runs), title if tag.startswith("h") else normal))
            flow.append(Spacer(1, 8))
        SimpleDocTemplate(out).build(flow)
        return out.getvalue(), "application/pdf"
    raise ValueError("UNSUPPORTED_FORMAT")
