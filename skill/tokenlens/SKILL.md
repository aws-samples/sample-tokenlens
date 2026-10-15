---
name: tokenlens
description: Reduce token consumption by compressing large tool output, file reads, and request bodies, and by trimming filler from responses. Use when returning large tool results (search, shell, logs, database rows), reading large files into context, or producing a long response.
license: MIT-0
compatibility: Requires the TokenLens MCP server (command "tokenlens mcp") to be registered with the agent.
metadata:
  author: AWS Professional Services
  version: "0.2.0"
---

# TokenLens

TokenLens reduces token consumption before content reaches the model. It runs
locally, makes no network calls, and is fail-open: if a tool cannot help, the
original content is returned unchanged, so using it is always safe.

The tools below come from the TokenLens MCP server. If they are not available,
the server is not registered — see the bundle README to install it.

## When and how to use the tools

- **Large tool output** — before returning a large tool result (search output,
  shell output, database rows, logs, pasted blocks) to the model, pass it
  through `tokenlens_compress` and return the compressed text.

- **Large file reads** — read large files with `tokenlens_read_file` instead of
  a raw file read, so the content is compressed at the source. It is read-only.

- **Full request bodies** — for an outbound request body or an inter-agent
  handoff, use `tokenlens_optimize_request`.

- **Completed responses** — strip conversational filler from a finished long
  response with `tokenlens_postprocess_response`. Code blocks and inline code
  are preserved verbatim.

- **Savings and retrieval** — report accumulated token savings with
  `tokenlens_stats`. When the Reversible Content Store (RCS) is enabled, use
  `tokenlens_retrieve` to recover omitted original detail by retrieval ID.

## Safety

These tools never fabricate content. Compression preserves code, commands,
file paths, error messages, and identifiers verbatim, and only removes or
samples low-value content. If a result would not be smaller, the original is
returned unchanged.
