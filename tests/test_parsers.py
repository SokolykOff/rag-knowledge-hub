import io

from openpyxl import Workbook

from app.ingest.chunking import chunk_segments
from app.ingest.parsers import UnsupportedFormat, parse_document
from app.ingest.pipeline import sha256_hex

from .helpers import DATA


def _xlsx() -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Прайс"
    ws.append(["Артикул", "Наименование", "Цена"])
    ws.append(["SKU-1", "Болт M8", 12])
    ws.append(["SKU-2", "Гайка M8", 5.0])
    ws2 = wb.create_sheet("Склады")
    ws2.append(["Город", "Адрес"])
    ws2.append(["Казань", "ул. Центральная, 1"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_excel_every_sheet_with_headers_and_sheet_name():
    segs = parse_document("price.xlsx", _xlsx())
    assert {s.sheet for s in segs} == {"Прайс", "Склады"}
    price = next(s for s in segs if s.sheet == "Прайс")
    assert "Артикул: SKU-1" in price.text and "Цена: 12" in price.text and "Наименование: Гайка M8" in price.text
    assert "Цена: 5;" not in price.text and "Цена: 5" in price.text  # 5.0 rendered as 5
    chunks = chunk_segments(segs)
    assert all(c.sheet for c in chunks) and any("Лист «Склады»" in c.content for c in chunks)


def test_csv_semicolon_and_cp1251():
    raw = "Артикул;Название;Цена\nA-100;Диск;250\nA-101;Лента;90\n".encode("cp1251")
    segs = parse_document("items.csv", raw)
    assert "Артикул: A-100" in segs[0].text and "Название: Диск" in segs[0].text


def test_pdf_pages_and_tables_from_real_file():
    segs = parse_document("warranty_policy.pdf", (DATA / "warranty_policy.pdf").read_bytes())
    pages = {s.page for s in segs}
    assert pages == {1, 2}
    text1 = " ".join(s.text for s in segs if s.page == 1)
    assert "24 месяца" in text1
    tables = [s for s in segs if s.kind == "table"]
    assert tables and tables[0].page == 2 and "Категория: Ноутбуки" in tables[0].text and "Гарантия: 24 месяца" in tables[0].text


def test_docx_sections_and_tables():
    segs = parse_document("contract.docx", (DATA / "contract_DG-2024-17.docx").read_bytes())
    pay = next(s for s in segs if s.section and s.section.endswith("2. Цена и порядок оплаты"))
    assert pay.section == "Договор поставки № ДГ-2024/17 › 2. Цена и порядок оплаты"  # title stays in the path
    assert "30 календарных дней" in pay.text
    assert "ДГ-2024/17" in chunk_segments([pay])[0].content  # so the contract number is searchable from every clause
    assert any(s.kind == "table" and "Артикул: SKU-48213" in s.text for s in segs)


def test_markdown_heading_path_and_chunk_prefix():
    md = b"# Policy\n\n## Vacation\nTwenty eight days.\n\n## Travel\nPer diem 700."
    segs = parse_document("p.md", md)
    assert [s.section for s in segs] == ["Policy › Vacation", "Policy › Travel"]
    chunks = chunk_segments(segs)
    assert chunks[0].content.startswith("Policy › Vacation")


def test_long_text_is_split_with_limits():
    para = ". ".join(f"Sentence number {i} has some filler words" for i in range(200))
    chunks = chunk_segments(parse_document("long.txt", para.encode()), max_chars=500, overlap=50)
    assert len(chunks) > 5 and all(len(c.content) <= 700 for c in chunks)
    assert [c.index for c in chunks] == list(range(len(chunks)))


def test_unsupported_and_sha():
    try:
        parse_document("x.exe", b"MZ")
    except UnsupportedFormat:
        pass
    else:
        raise AssertionError
    assert sha256_hex(b"abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
