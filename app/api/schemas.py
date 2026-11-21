from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    # Identity (tenant / role) is NEVER taken from a request body: unknown fields are rejected.
    model_config = ConfigDict(extra="forbid")


class ChatRequest(StrictModel):
    question: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=5, ge=1, le=10)
    mode: Literal["hybrid_rrf", "vector_only"] = "hybrid_rrf"
    use_cache: bool = True


class FeedbackRequest(StrictModel):
    rating: Literal[1, -1]
    comment: str | None = Field(default=None, max_length=1000)


class SimulateLeadRequest(StrictModel):
    title: str = Field(default="Запрос на коммерческое предложение: артикул SKU-48213", max_length=300)
    comments: str = Field(default="Клиент спрашивает цену, остаток и гарантийный срок ноутбуков.", max_length=2000)
    lead_id: int | None = Field(default=None, ge=1)
