"""Server-side identity. Tenant and role come ONLY from the bearer token table."""
from __future__ import annotations

import hmac
from dataclasses import dataclass

ROLES = ("guest", "employee", "manager", "admin")
RANK = {name: i for i, name in enumerate(ROLES)}


@dataclass(frozen=True)
class Principal:
    tenant_id: str
    role: str

    @property
    def rank(self) -> int:
        return RANK[self.role]

    def at_least(self, role: str) -> bool:
        return self.rank >= RANK[role]


def parse_role_tokens(raw: str) -> dict[str, Principal]:
    """`token:tenant:role,token2:tenant:role` -> {token: Principal}."""
    table: dict[str, Principal] = {}
    for item in (raw or "").split(","):
        item = item.strip()
        if not item:
            continue
        parts = item.split(":")
        if len(parts) != 3:
            raise ValueError(f"bad RAG_ROLE_TOKENS entry (expected token:tenant:role): {item!r}")
        token, tenant, role = (p.strip() for p in parts)
        if role not in RANK:
            raise ValueError(f"unknown role {role!r}; allowed: {ROLES}")
        if not token or not tenant:
            raise ValueError("token and tenant must be non-empty")
        table[token] = Principal(tenant_id=tenant, role=role)
    return table


def demo_tokens() -> dict[str, Principal]:
    return {f"demo-{t}-{r}": Principal(t, r) for t in ("acme", "globex") for r in ROLES}


def authenticate(token: str | None, table: dict[str, Principal]) -> Principal | None:
    if not token:
        return None
    found: Principal | None = None
    for known, principal in table.items():  # constant-time compare against every token
        if hmac.compare_digest(known.encode(), token.encode()):
            found = principal
    return found
