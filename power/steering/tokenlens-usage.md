# TokenLens Usage Guidance

Use TokenLens tools to reduce token consumption before content reaches the
model. TokenLens is local, deterministic, and fail-open, so applying it is
safe: if a tool cannot help, it returns the original content unchanged.

## Compress large tool output

Before returning a large tool result to the model, pass it through
`tokenlens_compress`. This applies to search results, shell command output,
database rows, build and test logs, and pasted blocks. Return the compressed
text in place of the raw output.

## Read large files compressed

When you need a large file in context, call `tokenlens_read_file` instead of a
raw file read. It reads the file and compresses it at the source (JSON arrays,
code skeletons, logs, and diffs), so token-heavy content never enters context
uncompressed. It is read-only and never writes or executes.

## Optimize inter-agent handoffs

When handing a large artifact from one agent or workflow stage to the next
(for example, passing one sub-agent's output to another, or forwarding
shared context between sub-agents), run the full request body through
`tokenlens_optimize_request` before passing it on. This compresses large
message content and can route and trim the request as configured.

## Strip filler from completed responses

For a finished long response, or an inter-agent handoff message, call
`tokenlens_postprocess_response` to remove conversational filler (preambles
and closing offers). Fenced code blocks and inline code are preserved verbatim.

## Report savings

Call `tokenlens_stats` to report accumulated token savings, including the
per-stage breakdown and, where routing is enabled, the per-tier counts.
