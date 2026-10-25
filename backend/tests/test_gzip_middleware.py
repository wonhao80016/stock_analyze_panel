"""Compression on, except where it would hold a stream back.

GZipMiddleware does not flush its deflate buffer per chunk, so compressing a
progressively-consumed stream delays every event until the stream closes. The
realtime feed is excluded by Starlette (text/event-stream); the ndjson build
stream marks itself identity, and this pins both.
"""
from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.testclient import TestClient
from starlette.responses import StreamingResponse

BIG = {"rows": [{"symbol": f"{i}.TWSE", "close": 1.0} for i in range(400)]}


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.add_middleware(GZipMiddleware, minimum_size=1024)

    @app.get("/big")
    def big():
        return BIG

    @app.get("/small")
    def small():
        return {"ok": True}

    @app.get("/events")
    def events():
        def gen():
            yield "data: one\n\n"
            yield "data: two\n\n"
        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.get("/ndjson")
    def ndjson():
        def gen():
            for i in range(200):
                yield json.dumps({"step": i}) + "\n"
        return StreamingResponse(
            gen(),
            media_type="application/x-ndjson",
            headers={"Content-Encoding": "identity"},
        )

    return TestClient(app)


def test_a_large_json_response_is_compressed(client):
    r = client.get("/big", headers={"Accept-Encoding": "gzip"})
    assert r.status_code == 200
    assert r.headers["content-encoding"] == "gzip"
    assert r.json() == BIG
    raw = len(json.dumps(BIG).encode())
    assert int(r.headers["content-length"]) < raw / 2


def test_a_small_response_is_left_alone(client):
    r = client.get("/small", headers={"Accept-Encoding": "gzip"})
    assert r.headers.get("content-encoding") != "gzip"


def test_a_client_that_did_not_ask_gets_plain_bytes(client):
    r = client.get("/big", headers={"Accept-Encoding": "identity"})
    assert r.headers.get("content-encoding") != "gzip"
    assert r.json() == BIG


def test_the_event_stream_is_never_compressed(client):
    """Starlette excludes text/event-stream; the realtime feed depends on it."""
    r = client.get("/events", headers={"Accept-Encoding": "gzip"})
    assert r.headers.get("content-encoding") != "gzip"
    assert "data: one" in r.text


def test_the_ndjson_stream_opts_out_and_stays_readable(client):
    r = client.get("/ndjson", headers={"Accept-Encoding": "gzip"})
    assert r.headers.get("content-encoding") == "identity"
    lines = [line for line in r.text.splitlines() if line]
    assert len(lines) == 200
    assert json.loads(lines[0]) == {"step": 0}


def test_compression_is_registered_on_the_real_app():
    """A middleware nobody registered compresses nothing."""
    from app.main import app

    assert any(m.cls is GZipMiddleware for m in app.user_middleware)
