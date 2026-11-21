import pytest

from app.core.resilience import CircuitBreaker, CircuitOpenError, TransientError
from app.integrations.bitrix24 import (
    Bitrix24Client, derive_idempotency_key, expand_bracket_keys, extract_event, format_draft_comment, simulated_lead_payload, verify_application_token,
)


def test_bracket_form_payload_is_expanded():
    flat = {"event": "ONCRMLEADADD", "data[FIELDS][ID]": "77", "auth[application_token]": "tok", "auth[domain]": "x.bitrix24.ru"}
    p = expand_bracket_keys(flat)
    assert p["data"]["FIELDS"]["ID"] == "77" and p["auth"]["application_token"] == "tok"
    info = extract_event(p)
    assert (info.event, info.entity_type, info.entity_id) == ("ONCRMLEADADD", "lead", "77")


def test_application_token_check():
    p = simulated_lead_payload("secret", 5, "t", "c")
    assert verify_application_token(p, "secret")
    assert not verify_application_token(p, "other") and not verify_application_token({}, "secret") and not verify_application_token(p, "")


def test_idempotency_key_header_wins_and_auto_key_is_stable_and_token_free():
    p1, p2 = simulated_lead_payload("A", 5, "t", "c"), simulated_lead_payload("B", 5, "t", "c")
    assert derive_idempotency_key("abc-1", p1) == "abc-1"
    assert derive_idempotency_key(None, p1) == derive_idempotency_key(None, p2)  # token does not influence the key
    assert derive_idempotency_key(None, p1) != derive_idempotency_key(None, simulated_lead_payload("A", 6, "t", "c"))


async def test_dry_run_client_makes_no_http_calls():
    client = Bitrix24Client("", dry_run=True)
    res = await client.add_timeline_comment("lead", "9", "hello")
    assert res["dry_run"] and res["method"] == "crm.timeline.comment.add"
    assert res["params"]["fields"] == {"ENTITY_ID": "9", "ENTITY_TYPE": "lead", "COMMENT": "hello"}


def test_draft_comment_formats():
    cites = [{"filename": "a.md", "location": "стр. 1"}, {"filename": "a.md", "location": "стр. 1"}]
    text = format_draft_comment("Ответ", cites, False)
    assert "Ответ" in text and text.count("a.md") == 1
    assert "не найдено" in format_draft_comment("", [], True)


async def test_live_client_retries_429_with_retry_after_and_opens_circuit():
    httpx = pytest.importorskip("httpx")
    seen, slept = [], []

    def handler(request):
        seen.append(request.url.path)
        if len(seen) == 1:
            return httpx.Response(429, headers={"Retry-After": "3"}, json={"error": "QUERY_LIMIT_EXCEEDED"})
        return httpx.Response(200, json={"result": 123})

    async def sleep(s):
        slept.append(s)

    client = Bitrix24Client("https://demo.bitrix24.ru/rest/1/token/", dry_run=False, transport=httpx.MockTransport(handler), sleep=sleep)
    out = await client.add_timeline_comment("deal", "5", "x")
    assert out == {"result": 123} and slept == [3.0] and seen[0].endswith("crm.timeline.comment.add.json")

    def always_500(request):
        return httpx.Response(500, json={})

    bad = Bitrix24Client("https://demo.bitrix24.ru/rest/1/token/", dry_run=False, max_retries=1, breaker=CircuitBreaker(2, 60), transport=httpx.MockTransport(always_500), sleep=sleep)
    for _ in range(2):
        with pytest.raises(TransientError):
            await bad.call("crm.lead.get", {"id": 1})
    with pytest.raises(CircuitOpenError):
        await bad.call("crm.lead.get", {"id": 1})
