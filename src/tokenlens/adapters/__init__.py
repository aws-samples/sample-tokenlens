"""TokenLens integration adapters.

Each adapter is a thin surface over the core API in ``tokenlens.api``:

- ``proxy``      : keyed HTTP proxy for environments that terminate the model call
- ``mcp_server`` : Model Context Protocol server for any MCP-capable IDE/agent

The core optimization engine (compressors, pipeline, metrics) has no knowledge
of these adapters.
"""
