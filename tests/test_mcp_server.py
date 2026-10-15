import json

from tokenlens.adapters.mcp_server import create_mcp_server
from tokenlens.config import Config


def _config(tmp_path) -> Config:
    return Config(metrics={"enabled": True, "dir": str(tmp_path / ".tokenlens")})


def _big_json() -> str:
    return json.dumps([{"id": i, "name": f"row-{i}"} for i in range(200)])


def _structured(call_result):
    """Extract the structured dict from a FastMCP call_tool return value.

    Different MCP SDK versions return either a dict or a
    (content_blocks, structured_output) tuple.
    """
    if isinstance(call_result, tuple):
        return call_result[1]
    return call_result


async def test_tools_are_registered(tmp_path):
    server = create_mcp_server(_config(tmp_path))
    tools = await server.list_tools()
    names = {tool.name for tool in tools}
    assert {
        "tokenlens_compress",
        "tokenlens_optimize_request",
        "tokenlens_stats",
    } <= names


async def test_compress_tool_reduces_tokens(tmp_path):
    server = create_mcp_server(_config(tmp_path))
    result = _structured(
        await server.call_tool("tokenlens_compress", {"text": _big_json()})
    )
    assert result["applied"]
    assert result["tokens_saved"] > 0


async def test_postprocess_response_tool(tmp_path):
    config = Config(
        metrics={"enabled": True, "dir": str(tmp_path / ".tokenlens")},
        postprocess={"enabled": True},
    )
    server = create_mcp_server(config)
    text = "Sure, I'd be happy to help! The answer is 42."
    result = _structured(
        await server.call_tool("tokenlens_postprocess_response", {"text": text})
    )
    assert result["applied"]
    assert result["tokens_saved"] > 0
    assert not result["text"].lower().startswith("sure")


async def test_stats_tool_reflects_compression(tmp_path):
    server = create_mcp_server(_config(tmp_path))
    await server.call_tool("tokenlens_compress", {"text": _big_json()})
    stats = _structured(await server.call_tool("tokenlens_stats", {}))
    assert stats["requests_optimized"] == 1
    assert stats["input_tokens_saved"] > 0


async def test_optimize_request_tool(tmp_path):
    server = create_mcp_server(_config(tmp_path))
    request = {
        "model": "claude-3-5-sonnet",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "t1", "content": _big_json()}
                ],
            }
        ],
    }
    result = _structured(
        await server.call_tool("tokenlens_optimize_request", {"request": request})
    )
    assert result["report"]["input_tokens_saved"] > 0


async def test_read_file_tool_compresses(tmp_path):
    server = create_mcp_server(_config(tmp_path))
    target = tmp_path / "data.json"
    target.write_text(_big_json(), encoding="utf-8")

    result = _structured(
        await server.call_tool("tokenlens_read_file", {"path": str(target)})
    )
    assert result["error"] is None
    assert result["applied"]
    assert result["tokens_saved"] > 0


async def test_read_file_missing_file(tmp_path):
    server = create_mcp_server(_config(tmp_path))
    result = _structured(
        await server.call_tool(
            "tokenlens_read_file", {"path": str(tmp_path / "nope.json")}
        )
    )
    assert result["error"] == "not a file"
    assert not result["applied"]


async def test_read_file_respects_max_bytes(tmp_path):
    server = create_mcp_server(_config(tmp_path))
    target = tmp_path / "big.json"
    target.write_text(_big_json(), encoding="utf-8")
    result = _structured(
        await server.call_tool(
            "tokenlens_read_file", {"path": str(target), "max_bytes": 10}
        )
    )
    assert result["error"] is not None
    assert "max_bytes" in result["error"]


async def test_read_file_non_utf8_passes_through(tmp_path):
    # A binary / non-UTF-8 file must return the error result without
    # compressing or raising (fail-open pass-through). Requirement 3.6.
    server = create_mcp_server(_config(tmp_path))
    target = tmp_path / "image.bin"
    target.write_bytes(b"\xff\xfe\x00\x80\x81\x82 not valid utf-8 \xfd\xfc")

    result = _structured(
        await server.call_tool("tokenlens_read_file", {"path": str(target)})
    )

    assert result["error"] == "binary or non-UTF-8 file; not compressed"
    assert not result["applied"]
    assert result["text"] == ""
    assert result["tokens_saved"] == 0
    assert result["compressor"] == "none"


async def test_capture_records_when_enabled(tmp_path):
    from tokenlens.config import Config

    config = Config(
        metrics={"enabled": True, "dir": str(tmp_path / ".tokenlens")},
        capture={"enabled": True, "dir": str(tmp_path / "cap")},
    )
    server = create_mcp_server(config)
    await server.call_tool("tokenlens_compress", {"text": _big_json()})

    capture_file = tmp_path / "cap" / "captures.jsonl"
    assert capture_file.exists()
    lines = capture_file.read_text().strip().splitlines()
    assert len(lines) == 1
    assert "mcp:compress" in lines[0]


async def test_rcs_retrieve_roundtrip(tmp_path):
    from tokenlens.config import Config

    config = Config(
        metrics={"enabled": True, "dir": str(tmp_path / ".tokenlens")},
        rcs={"enabled": True, "dir": str(tmp_path / "rcs")},
    )
    server = create_mcp_server(config)
    original = _big_json()

    compressed = _structured(
        await server.call_tool("tokenlens_compress", {"text": original})
    )
    # The store should attach a retrieve_id and the model-facing note.
    assert "retrieve_id" in compressed
    assert "tokenlens_retrieve" in compressed["text"]

    retrieved = _structured(
        await server.call_tool(
            "tokenlens_retrieve", {"retrieve_id": compressed["retrieve_id"]}
        )
    )
    assert retrieved["found"] is True
    assert retrieved["content"] == original


async def test_rcs_retrieve_missing_key(tmp_path):
    from tokenlens.config import Config

    config = Config(rcs={"enabled": True, "dir": str(tmp_path / "rcs")})
    server = create_mcp_server(config)
    result = _structured(
        await server.call_tool("tokenlens_retrieve", {"retrieve_id": "nope"})
    )
    assert result["found"] is False


async def test_no_retrieve_id_when_rcs_disabled(tmp_path):
    server = create_mcp_server(_config(tmp_path))  # store disabled by default
    compressed = _structured(
        await server.call_tool("tokenlens_compress", {"text": _big_json()})
    )
    assert "retrieve_id" not in compressed


async def test_read_file_refuses_sensitive_path(tmp_path):
    # A real secret-looking file exists, but the guard must refuse it before
    # any read, returning the structured error without exposing contents.
    server = create_mcp_server(_config(tmp_path))
    secret = tmp_path / ".env"
    secret.write_text("API_KEY=super-secret-value\n", encoding="utf-8")

    result = _structured(
        await server.call_tool("tokenlens_read_file", {"path": str(secret)})
    )

    assert result["error"] == "refused: path points at a sensitive or disallowed file"
    assert result["applied"] is False
    assert result["text"] == ""
    assert "super-secret-value" not in result["text"]


async def test_read_file_refuses_ssh_key(tmp_path):
    server = create_mcp_server(_config(tmp_path))
    keydir = tmp_path / ".ssh"
    keydir.mkdir()
    key = keydir / "id_rsa"
    # Innocuous placeholder content: the guard refuses based on the path
    # (.ssh/ dir + id_rsa basename), not the file contents. Avoid embedding a
    # real key header so secret scanners do not flag this test fixture.
    key.write_text("placeholder-not-a-real-key\n", encoding="utf-8")

    result = _structured(
        await server.call_tool("tokenlens_read_file", {"path": str(key)})
    )

    assert result["error"] == "refused: path points at a sensitive or disallowed file"
    assert result["applied"] is False
    assert result["text"] == ""


async def test_read_file_routing_disabled_returns_original(tmp_path):
    config = _config(tmp_path)
    config.always_on.read_routing_enabled = False
    server = create_mcp_server(config)
    target = tmp_path / "data.json"
    content = _big_json()
    target.write_text(content, encoding="utf-8")

    result = _structured(
        await server.call_tool("tokenlens_read_file", {"path": str(target)})
    )

    assert result["error"] is None
    assert result["text"] == content
    assert result["applied"] is False
    assert result["tokens_saved"] == 0
