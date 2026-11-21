"""Tiny dependency-free HTTP helpers shared by smoke_test.py and demo.py (stdlib only)."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
import uuid

BASE = "http://localhost:8000"


def request(method: str, path: str, *, token: str | None = None, body=None, headers: dict | None = None, raw: bytes | None = None, base: str | None = None):
    url = (base or BASE) + path
    hdrs = dict(headers or {})
    data = raw
    if body is not None:
        data = json.dumps(body).encode()
        hdrs.setdefault("Content-Type", "application/json")
    if token:
        hdrs["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            payload = resp.read()
            return resp.status, (json.loads(payload) if payload else None), dict(resp.headers)
    except urllib.error.HTTPError as exc:
        payload = exc.read()
        try:
            return exc.code, json.loads(payload), dict(exc.headers)
        except ValueError:
            return exc.code, payload.decode(errors="replace"), dict(exc.headers)


def upload(token: str, filename: str, content: bytes, access_level: str, base: str | None = None):
    boundary = uuid.uuid4().hex
    parts = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="access_level"\r\n\r\n{access_level}\r\n'
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\nContent-Type: application/octet-stream\r\n\r\n'
    ).encode() + content + f"\r\n--{boundary}--\r\n".encode()
    return request("POST", "/api/v1/documents", token=token, raw=parts, headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}, base=base)


def stream_chat(token: str, question: str, base: str | None = None, **extra):
    """Consume the SSE endpoint; returns (answer_text, citations, done_payload)."""
    req = urllib.request.Request((base or BASE) + "/api/v1/chat/stream", data=json.dumps({"question": question, **extra}).encode(),
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"}, method="POST")
    answer, citations, done, event = "", [], {}, None
    with urllib.request.urlopen(req, timeout=60) as resp:
        for line in resp:
            line = line.decode().rstrip("\n")
            if line.startswith("event: "):
                event = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
                if event == "token":
                    answer += data
                elif event == "citations":
                    citations = data
                elif event == "done":
                    done = data
    return answer, citations, done


def wait_until(fn, timeout: float = 60.0, interval: float = 0.5, what: str = "condition"):
    end = time.time() + timeout
    while time.time() < end:
        value = fn()
        if value:
            return value
        time.sleep(interval)
    raise TimeoutError(f"timed out waiting for {what}")


def tok(tenant: str, role: str) -> str:
    return f"demo-{tenant}-{role}"
