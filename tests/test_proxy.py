import json

import httpx
import respx
from fastapi.testclient import TestClient

from tokenlens.config import Config
from tokenlens.adapters.proxy import create_app

UPSTREAM = "https://api.anthropic.com"


def _config(tmp_path) -> Config:
    return Config(
        compression={"enabled": True, "mode": "balanced"},
        output_constraints={"enabled": False, "level": "balanced"},
        prefix={"enabled": False, "min_prefix_tokens": 1024},
        proxy={
            "host": "127.0.0.1",
            "port": 9470,
            "upstream_base_url": UPSTREAM,
            "timeout_seconds": 30.0,
        },
        metrics={"enabled": True, "dir": str(tmp_path / ".tokenlens")},
    )


def _big_tool_result_request() -> dict:
    payload = json.dumps([{"id": i, "v": f"row-{i}"} for i in range(200)])
    return {
        "model": "claude-3-5-sonnet",
        "max_tokens": 1024,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "t1", "content": payload}
                ],
            }
        ],
    }


def test_health_endpoint(tmp_path):
    app = create_app(_config(tmp_path))
    client = TestClient(app)
    response = client.get("/tokenlens/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@respx.mock
def test_messages_are_optimized_before_forwarding(tmp_path):
    captured = {}

    def responder(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": "msg_1", "content": []})

    respx.post(f"{UPSTREAM}/v1/messages").mock(side_effect=responder)

    app = create_app(_config(tmp_path))
    client = TestClient(app)

    original = _big_tool_result_request()
    original_size = len(json.dumps(original))

    response = client.post(
        "/v1/messages",
        json=original,
        headers={"x-api-key": "test-key", "anthropic-version": "2023-06-01"},
    )

    assert response.status_code == 200
    # The forwarded body must be smaller than the original (compression applied).
    forwarded_size = len(json.dumps(captured["body"]))
    assert forwarded_size < original_size
    # The tool result content must have been shrunk.
    forwarded_content = captured["body"]["messages"][0]["content"][0]["content"]
    assert "__tokenlens_omitted__" in forwarded_content


@respx.mock
def test_api_key_header_is_forwarded(tmp_path):
    captured = {}

    def responder(request: httpx.Request) -> httpx.Response:
        captured["api_key"] = request.headers.get("x-api-key")
        return httpx.Response(200, json={"ok": True})

    respx.post(f"{UPSTREAM}/v1/messages").mock(side_effect=responder)

    app = create_app(_config(tmp_path))
    client = TestClient(app)
    client.post(
        "/v1/messages",
        json={"model": "m", "messages": [], "max_tokens": 10},
        headers={"x-api-key": "secret-key"},
    )
    assert captured["api_key"] == "secret-key"


@respx.mock
def test_metrics_recorded_after_request(tmp_path):
    respx.post(f"{UPSTREAM}/v1/messages").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    app = create_app(_config(tmp_path))
    client = TestClient(app)

    client.post("/v1/messages", json=_big_tool_result_request())

    stats = client.get("/tokenlens/stats").json()
    assert stats["requests_optimized"] == 1
    assert stats["input_tokens_saved"] > 0


@respx.mock
def test_fail_open_on_malformed_json(tmp_path):
    captured = {}

    def responder(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content
        return httpx.Response(200, json={"ok": True})

    respx.post(f"{UPSTREAM}/v1/messages").mock(side_effect=responder)

    app = create_app(_config(tmp_path))
    client = TestClient(app)

    # Send invalid JSON; proxy must forward the raw bytes unchanged.
    response = client.post(
        "/v1/messages",
        content=b"this is not json",
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 200
    assert captured["body"] == b"this is not json"


@respx.mock
def test_passthrough_other_paths(tmp_path):
    respx.get(f"{UPSTREAM}/v1/models").mock(
        return_value=httpx.Response(200, json={"data": ["claude"]})
    )
    app = create_app(_config(tmp_path))
    client = TestClient(app)
    response = client.get("/v1/models")
    assert response.status_code == 200
    assert response.json()["data"] == ["claude"]
