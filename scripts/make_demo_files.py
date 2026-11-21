"""Regenerates the binary demo fixtures (xlsx, docx, pdf) in app/seed/data. Needs requirements-dev.txt."""
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "app" / "seed" / "data"


def make_xlsx() -> None:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Прайс"
    ws.append(["Артикул", "Наименование", "Цена опт, руб", "Остаток"])
    for row in [
        ("SKU-48213", "Ноутбук ProBook 15", 68500, 12),
        ("SKU-48230", "Ноутбук ProBook 17", 82000, 5),
        ("CB-77120", "Кабель HDMI 2.1, 2 м", 610, 240),
        ("MN-20045", "Монитор UltraView 27", 19400, 18),
        ("KB-10033", "Клавиатура механическая K3", 4700, 64),
    ]:
        ws.append(row)
    ws2 = wb.create_sheet("Склады")
    ws2.append(["Склад", "Город", "Адрес", "Телефон"])
    ws2.append(["Склад №1", "Москва", "ул. Складочная, 1", "+7 495 111-22-33"])
    ws2.append(["Склад №2", "Казань", "ул. Магистральная, 15", "+7 843 222-33-44"])
    ws2.append(["Склад №3", "Екатеринбург", "ул. Заводская, 8", "+7 343 333-44-55"])
    wb.save(DATA / "price_list.xlsx")


def make_docx() -> None:
    from docx import Document

    d = Document()
    d.add_heading("Договор поставки № ДГ-2024/17", level=1)
    d.add_heading("1. Предмет договора", level=2)
    d.add_paragraph("Поставщик обязуется поставить, а Покупатель принять и оплатить компьютерное оборудование согласно спецификации.")
    d.add_heading("2. Цена и порядок оплаты", level=2)
    d.add_paragraph("Оплата производится в течение 30 календарных дней с даты поставки на основании выставленного счёта.")
    d.add_heading("3. Ответственность сторон", level=2)
    d.add_paragraph("За просрочку оплаты Покупатель уплачивает неустойку в размере 0,1% от неоплаченной суммы за каждый день просрочки.")
    d.add_heading("4. Срок действия", level=2)
    d.add_paragraph("Договор действует до 31 декабря 2027 года и автоматически продлевается на год, если ни одна из сторон не заявит о расторжении.")
    d.add_heading("Спецификация", level=2)
    t = d.add_table(rows=1, cols=3)
    for c, h in zip(t.rows[0].cells, ["Артикул", "Наименование", "Количество"]):
        c.text = h
    for row in [("SKU-48213", "Ноутбук ProBook 15", "20"), ("MN-20045", "Монитор UltraView 27", "20")]:
        cells = t.add_row().cells
        for c, v in zip(cells, row):
            c.text = v
    d.save(DATA / "contract_DG-2024-17.docx")


def make_pdf() -> None:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    pdfmetrics.registerFont(TTFont("DejaVu", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"))
    base = getSampleStyleSheet()["Normal"]
    body = ParagraphStyle("b", parent=base, fontName="DejaVu", fontSize=11, leading=15)
    head = ParagraphStyle("h", parent=body, fontSize=15, leading=20, spaceAfter=8)
    story = [
        Paragraph("Гарантийная политика ACME", head),
        Paragraph("Гарантийный срок на ноутбуки и мониторы составляет 24 месяца с даты покупки.", body),
        Spacer(1, 8),
        Paragraph("Гарантийный ремонт выполняется в срок до 14 рабочих дней с момента приёмки устройства в сервисный центр.", body),
        PageBreak(),
        Paragraph("Гарантийные сроки по категориям", head),
    ]
    table = Table(
        [["Категория", "Гарантия", "Срок ремонта"], ["Ноутбуки", "24 месяца", "14 рабочих дней"], ["Мониторы", "24 месяца", "14 рабочих дней"], ["Кабели", "6 месяцев", "замена"]],
        colWidths=[150, 120, 150],
    )
    table.setStyle(TableStyle([("FONTNAME", (0, 0), (-1, -1), "DejaVu"), ("GRID", (0, 0), (-1, -1), 0.7, colors.black)]))
    story.append(table)
    SimpleDocTemplate(str(DATA / "warranty_policy.pdf"), pagesize=A4).build(story)


if __name__ == "__main__":
    make_xlsx()
    make_docx()
    make_pdf()
    print("demo fixtures written to", DATA)
