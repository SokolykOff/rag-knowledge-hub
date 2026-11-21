"""Format-specific parsers. Output: list[Segment] preserving page / sheet / section."""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from pathlib import PurePath

SUPPORTED = {".pdf", ".xlsx", ".xlsm", ".csv", ".docx", ".md", ".markdown", ".txt"}
ROWS_PER_CHUNK = 8


class UnsupportedFormat(ValueError):
    pass


@dataclass
class Segment:
    text: str
    page: int | None = None
    sheet: str | None = None
    section: str | None = None
    kind: str = "text"  # text | table


def _cell(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return re.sub(r"\s+", " ", str(v)).strip()


def table_to_segments(rows: list[list], label: str, *, page=None, sheet=None, section=None, start_row: int = 1) -> list[Segment]:
    """Turn table rows into readable 'Column: value' lines, repeating the header context in every segment."""
    rows = [[_cell(c) for c in r] for r in rows]
    rows = [r for r in rows if any(r)]
    if len(rows) < 2:
        return [Segment(" | ".join(r), page, sheet, section, "table") for r in rows]
    header, body = rows[0], rows[1:]
    header = [h or f"col{i + 1}" for i, h in enumerate(header)]
    prefix = f"{label} | колонки: {', '.join(header)}"
    segs: list[Segment] = []
    for i in range(0, len(body), ROWS_PER_CHUNK):
        lines = []
        for j, row in enumerate(body[i : i + ROWS_PER_CHUNK]):
            pairs = [f"{h}: {v}" for h, v in zip(header, row) if v]
            lines.append(f"Строка {start_row + 1 + i + j}: " + "; ".join(pairs))
        segs.append(Segment(prefix + "\n" + "\n".join(lines), page, sheet, section, "table"))
    return segs


def parse_pdf(data: bytes) -> list[Segment]:
    import pdfplumber
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    texts = [(p.extract_text() or "").strip() for p in reader.pages]
    segs: list[Segment] = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for i, page in enumerate(pdf.pages):
            number = i + 1
            text = texts[i] if i < len(texts) else ""
            if not text:  # fallback when pypdf cannot read the page
                text = (page.extract_text() or "").strip()
            if text:
                segs.append(Segment(text, page=number))
            try:
                tables = page.extract_tables()
            except Exception:  # noqa: BLE001 - table detection is best-effort
                tables = []
            for t_idx, table in enumerate(tables, start=1):
                segs.extend(table_to_segments(table, f"Таблица {t_idx}, стр. {number}", page=number))
    return segs


def parse_xlsx(data: bytes) -> list[Segment]:
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    segs: list[Segment] = []
    for ws in wb.worksheets:
        rows = [list(r) for r in ws.iter_rows(values_only=True)]
        segs.extend(table_to_segments(rows, f"Лист «{ws.title}»", sheet=ws.title))
    wb.close()
    return segs


def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "cp1251"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def parse_csv(data: bytes, filename: str) -> list[Segment]:
    text = _decode(data)
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.reader(io.StringIO(text), dialect))
    sheet = PurePath(filename).stem
    return table_to_segments(rows, f"Таблица «{sheet}»", sheet=sheet)


def _heading_level(style_name: str) -> int | None:
    """'Heading 2' / 'Заголовок 2' -> 2, 'Title' -> 0, anything else -> None."""
    name = (style_name or "").lower()
    if name == "title":
        return 0
    if name.startswith("heading") or name.startswith("заголовок"):
        m = re.search(r"(\d+)\s*$", name)
        return int(m.group(1)) if m else 1
    return None


def parse_docx(data: bytes) -> list[Segment]:
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    doc = Document(io.BytesIO(data))
    segs: list[Segment] = []
    stack: list[tuple[int, str]] = []  # heading path: the document title stays part of every section
    buf: list[str] = []

    def section() -> str | None:
        return " › ".join(t for _, t in stack) or None

    def flush():
        if buf:
            segs.append(Segment("\n".join(buf), section=section()))
            buf.clear()

    for child in doc.element.body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            p = Paragraph(child, doc)
            text = p.text.strip()
            if not text:
                continue
            level = _heading_level(p.style.name if p.style is not None else "")
            if level is not None:
                flush()
                while stack and stack[-1][0] >= level:
                    stack.pop()
                stack.append((level, text))
            else:
                buf.append(text)
        elif tag == "tbl":
            flush()
            table = Table(child, doc)
            rows = [[c.text for c in r.cells] for r in table.rows]
            segs.extend(table_to_segments(rows, f"Таблица в разделе «{section() or 'документ'}»", section=section()))
    flush()
    return segs


_MD_HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")


def parse_markdown(data: bytes) -> list[Segment]:
    text = _decode(data)
    segs: list[Segment] = []
    stack: list[tuple[int, str]] = []
    buf: list[str] = []

    def flush():
        body = "\n".join(buf).strip()
        if body:
            segs.append(Segment(body, section=" › ".join(t for _, t in stack) or None))
        buf.clear()

    for line in text.splitlines():
        m = _MD_HEADING.match(line)
        if m:
            flush()
            level = len(m.group(1))
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, m.group(2)))
        else:
            buf.append(line)
    flush()
    return segs


def parse_document(filename: str, data: bytes) -> list[Segment]:
    ext = PurePath(filename).suffix.lower()
    if ext not in SUPPORTED:
        raise UnsupportedFormat(f"unsupported file type {ext!r}; supported: {sorted(SUPPORTED)}")
    if ext == ".pdf":
        return parse_pdf(data)
    if ext in {".xlsx", ".xlsm"}:
        return parse_xlsx(data)
    if ext == ".csv":
        return parse_csv(data, filename)
    if ext == ".docx":
        return parse_docx(data)
    if ext in {".md", ".markdown"}:
        return parse_markdown(data)
    text = _decode(data).strip()
    return [Segment(text)] if text else []
