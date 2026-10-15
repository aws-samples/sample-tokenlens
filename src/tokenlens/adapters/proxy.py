"""FastAPI interception proxy for the Anthropic Messages API.

The proxy sits at ``http://127.0.0.1:<port>`` and forwards traffic to the
Anthropic API. Requests to ``/v1/messages`` are run through the optimization
pipeline before forwarding; every other path is proxied untouched.

Safety model: the proxy is fail-open. If optimization raises, the original
request body is forwarded unchanged so the user is never blocked by a bug in
TokenLens.

Use this adapter only in environments where TokenLens terminates the model
call and holds a credential/gateway. For IDEs/agents that route through an
internal gateway or that you cannot re-point, use the MCP server adapter
instead.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import Response, StreamingResponse

from .. import __version__
from ..capture import CaptureRecorder
from ..config import Config
from ..metrics import MetricsRecorder
from ..pipeline import OptimizationPipeline

# Hop-by-hop and length headers we must not copy verbatim to the upstream or
# back to the client; httpx/starlette manage these.
_SKIP_REQUEST_HEADERS = {"host", "content-length", "accept-encoding"}
_SKIP_RESPONSE_HEADERS = {"content-length", "content-encoding", "transfer-encoding"}


def create_app(config: Config) -> FastAPI:
    pipeline = OptimizationPipeline(config)
    metrics = MetricsRecorder(config.metrics.dir, config.metrics.enabled)
    capture = CaptureRecorder(
        config.capture.dir, config.capture.enabled, config.capture.store_content
    )
    client = httpx.AsyncClient(
        base_url=config.proxy.upstream_base_url,
        timeout=config.proxy.timeout_seconds,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        await client.aclose()

    app = FastAPI(title="TokenLens Proxy", version=__version__, lifespan=lifespan)
    app.state.config = config
    app.state.metrics = metrics

    @app.get("/tokenlens/health")
    async def health() -> dict:
        return {"status": "ok", "version": __version__}

    @app.get("/tokenlens/stats")
    async def stats() -> dict:
        return metrics.stats()

    @app.post("/v1/messages")
    async def messages(request: Request) -> Response:
        raw_body = await request.body()
        forward_body = raw_body
        report = None

        # Optimize the request body, failing open on any error.
        try:
            payload = json.loads(raw_body)
            optimized, report = pipeline.process(payload)
            forward_body = json.dumps(optimized).encode("utf-8")
        except Exception:  # noqa: BLE001 - proxy request path must fail open
            forward_body = raw_body
            report = None

        headers = _forward_request_headers(request.headers)
        is_stream = _wants_stream(forward_body)

        upstream = client.build_request(
            "POST", "/v1/messages", content=forward_body, headers=headers
        )
        upstream_response = await client.send(upstream, stream=is_stream)

        if report is not None:
            decision = report.routing_decision
            metrics.record(
                metrics.make_event(
                    model=report.model,
                    input_tokens_before=report.input_tokens_before,
                    input_tokens_after=report.input_tokens_after,
                    stage_savings=report.stage_savings,
                    routing_tier=decision.tier if decision else None,
                    routed_model=decision.routed_model if decision else None,
                )
            )
            capture.record(
                source="proxy",
                content_type=f"request:{report.model}",
                original_tokens=report.input_tokens_before,
                compressed_tokens=report.input_tokens_after,
                applied=report.input_tokens_saved > 0,
            )

        response_headers = _forward_response_headers(upstream_response.headers)

        if is_stream:

            async def body_iterator():
                async for chunk in upstream_response.aiter_raw():
                    yield chunk
                await upstream_response.aclose()

            return StreamingResponse(
                body_iterator(),
                status_code=upstream_response.status_code,
                headers=response_headers,
                media_type=upstream_response.headers.get("content-type"),
            )

        content = await upstream_response.aread()
        await upstream_response.aclose()
        return Response(
            content=content,
            status_code=upstream_response.status_code,
            headers=response_headers,
            media_type=upstream_response.headers.get("content-type"),
        )

    @app.api_route(
        "/{path:path}",
        methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    )
    async def passthrough(request: Request, path: str) -> Response:
        raw_body = await request.body()
        headers = _forward_request_headers(request.headers)
        upstream = client.build_request(
            request.method,
            f"/{path}",
            content=raw_body,
            headers=headers,
            params=dict(request.query_params),
        )
        upstream_response = await client.send(upstream)
        content = await upstream_response.aread()
        await upstream_response.aclose()
        return Response(
            content=content,
            status_code=upstream_response.status_code,
            headers=_forward_response_headers(upstream_response.headers),
            media_type=upstream_response.headers.get("content-type"),
        )

    return app


def _forward_request_headers(headers) -> dict[str, str]:
    return {
        key: value
        for key, value in headers.items()
        if key.lower() not in _SKIP_REQUEST_HEADERS
    }


def _forward_response_headers(headers) -> dict[str, str]:
    return {
        key: value
        for key, value in headers.items()
        if key.lower() not in _SKIP_RESPONSE_HEADERS
    }


def _wants_stream(body: bytes) -> bool:
    try:
        return bool(json.loads(body).get("stream", False))
    except (json.JSONDecodeError, AttributeError, TypeError):
        return False
