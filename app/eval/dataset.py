"""Built-in reference dataset: demo corpus (PDF / Excel / DOCX / Markdown / CSV) + questions with ground truth."""
from __future__ import annotations

from dataclasses import dataclass

EVAL_TENANT = "__eval__"
CORPUS = [  # filenames from app/seed/data; all indexed at admin level inside the isolated eval tenant
    "faq_public.md", "catalog.csv", "warranty_policy.pdf", "hr_policy.md", "price_list.xlsx", "contract_DG-2024-17.docx", "salary_matrix.md",
]


def large_catalog_csv() -> bytes:
    """Deterministic 96-row product catalogue: near-identical rows that differ mainly by article code,
    the classic case where pure vector search confuses neighbours and exact FTS wins."""
    kinds = ["Кабель HDMI", "Кабель USB-C", "Адаптер питания", "Переходник DisplayPort", "Удлинитель Ethernet", "Разветвитель USB"]
    lines = ["Артикул;Наименование;Цена, руб;Остаток"]
    for i in range(96):
        code = f"AR-{31000 + i * 7}"
        lines.append(f"{code};{kinds[i % len(kinds)]} модель {i % 5 + 1};{300 + (i * 37) % 900};{(i * 13) % 90 + 5}")
    return ("\n".join(lines) + "\n").encode()


EXTRA_FILES = {"eval_catalog.csv": large_catalog_csv}


@dataclass(frozen=True)
class EvalQuestion:
    question: str
    must_contain: tuple[str, ...]  # a retrieved chunk is relevant if it contains ANY of these (case-insensitive)
    kind: str = "semantic"  # semantic | code | table


QUESTIONS: list[EvalQuestion] = [
    EvalQuestion("Сколько календарных дней отпуска положено сотруднику?", ("28 календарных",)),
    EvalQuestion("Цена и остаток по артикулу SKU-48213", ("SKU-48213",), "code"),
    EvalQuestion("В какой срок нужно оплатить поставку по договору ДГ-2024/17?", ("30 календарных дней",), "code"),
    EvalQuestion("Какая неустойка за просрочку оплаты по договору ДГ-2024/17?", ("0,1%",), "code"),
    EvalQuestion("Какой гарантийный срок на ноутбуки?", ("24 месяца",)),
    EvalQuestion("Можно ли вернуть товар и в какие сроки?", ("14 дней",)),
    EvalQuestion("Сколько составляют суточные в командировке по России?", ("700 рублей",)),
    EvalQuestion("Какой адрес склада в Казани?", ("Казань",), "table"),
    EvalQuestion("Сколько осталось на складе по артикулу CB-77120?", ("CB-77120",), "code"),
    EvalQuestion("За сколько рабочих дней выполняется гарантийный ремонт?", ("14 рабочих дней",)),
    EvalQuestion("Какой оклад у грейда Senior?", ("Senior",), "table"),
    EvalQuestion("Сколько дней в неделю разрешена удалённая работа?", ("2 дней в неделю", "2 дня")),
    EvalQuestion("С какой суммы заказа доставка бесплатная?", ("5000 рублей",)),
    EvalQuestion("На какой email писать в поддержку?", ("support@acme.example",), "code"),
    EvalQuestion("Сколько стоит артикул AR-31189?", ("AR-31189",), "code"),
    EvalQuestion("Остаток по артикулу AR-31434 на складе", ("AR-31434",), "code"),
    EvalQuestion("Цена AR-31266", ("AR-31266",), "code"),
    EvalQuestion("Что за товар AR-31567 и сколько он стоит?", ("AR-31567",), "code"),
    EvalQuestion("Есть ли на складе AR-31084?", ("AR-31084",), "code"),
    EvalQuestion("Наличие артикула AR-31616", ("AR-31616",), "code"),
]
