---
name: "tokenlens"
displayName: "TokenLens Token Optimization"
description: "Reduce LLM token consumption by compressing tool output, file reads, and requests, and trimming response filler. Local, deterministic, fail-open."
keywords:
  - "compress"
  - "compression"
  - "token"
  - "tokens"
  - "token budget"
  - "large file"
  - "tool output"
  - "context"
  - "optimize request"
  - "handoff"
  - "agent handoff"
  - "trim response"
author: "AWS Professional Services"
---

# TokenLens

TokenLens reduces token consumption before content reaches the model. It runs
locally, makes no network calls, and is fail-open. Use it before placing large
content into model context.

# Onboarding

## Verify installation

```bash
tokenlens --version
```

If unavailable, stop and ask the user to install the exact approved release:

```bash
# Run from the downloaded TokenLens 0.2.0 release directory.
pipx install --pip-args="--constraint ./constraints-release.txt" \
  "aws-samples-tokenlens[mcp,tokenizers]==0.2.0"
# or
uv tool install --constraints ./constraints-release.txt \
  --with "mcp==1.29.1" --with "tiktoken==0.13.0" "aws-samples-tokenlens==0.2.0"
```

For reliable always-on Kiro use, run the supported installer from each
workspace:

```bash
tokenlens kiro install --workspace .
tokenlens kiro doctor --workspace .
```

# Using TokenLens

Detailed guidance is in `steering/tokenlens-usage.md`.

- Pass large tool output through `tokenlens_compress`.
- Read large UTF-8 files with `tokenlens_read_file`.
- Optimize Anthropic request bodies with `tokenlens_optimize_request`.
- Trim completed responses with `tokenlens_postprocess_response` when requested.
- Retrieve omitted content from the Reversible Content Store with `tokenlens_retrieve`.
- Report measured savings with `tokenlens_stats`.

# Runtime boundaries

Power activation is keyword-based. The CLI installer supplies true always-on
tool availability, steering, and large-read enforcement.

- Kiro hook prefilter: 8 KB by default.
- MCP compressed-read threshold: 2,000 tokens by default.
- History trimming runs only through `tokenlens_optimize_request`.
- Generic file-read routing has its own toggle.
- Recursive steering save hooks are intentionally not shipped.
- Capture, RCS, response post-processing, and model routing remain opt-in.

All compression is local, profitability gated, and fail-open. Sensitive-path
refusals must not be bypassed.
