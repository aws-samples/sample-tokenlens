---
inclusion: always
---

# TokenLens Always-On Policy

Apply this policy on every task in Kiro IDE and Kiro CLI. TokenLens MCP tools
must be registered separately; use them when the decision rules below match.
Steering is advisory, while the installed PreToolUse hook enforces routing for
large text files.

## Retrieval decision rules

1. **Discover before reading.** Use code navigation, `grep`, file search, or
   directory listing to identify the smallest relevant target.
2. **Small text file (<8 KB):** native bounded read is acceptable.
3. **Large text file (>=8 KB):** do not use native `read`/`read_file`. Narrow
   with search first; if file contents are still needed, call
   `tokenlens_read_file`.
4. **Large pasted/tool output:** call `tokenlens_compress` before carrying it
   into further reasoning. This includes logs, diffs, query rows, and search
   results.
5. **Large Anthropic-style request body:** call `tokenlens_optimize_request`.
6. **Need omitted detail:** call `tokenlens_retrieve` with the returned retrieval
   ID instead of rereading or recompressing everything.
7. **Savings requested:** call `tokenlens_stats`; never invent token counts.

TokenLens is local, read-only for file reads, deterministic, and profitability
gated. If compression does not reduce tokens, it returns original text.
Sensitive-path refusals are intentional. Use Kiro's native reader for
binary/image/document content; TokenLens handles UTF-8 text only.

## Retrieval discipline

| Situation | Do | Do NOT |
| --- | --- | --- |
| Need one symbol | Search, then inspect relevant context | Read whole files blindly |
| Same search already run | Reuse prior finding | Repeat identical search |
| Build/test output | Inspect errors or bounded tail | Stream full output into context |
| Git inspection | Use bounded summaries | Dump unbounded diff or history |
| More than 3 searches | Retarget focused investigation | Accumulate raw output |
| Just edited a file | Validate with tests | Reread entire file |

Legacy sources such as `.sql`, `.pkb`, `.pks`, `.cbl`, `.dtsx`, and `.jcl`
often compress poorly. Prefer search and aggregation for them.

## Output discipline

- No filler or restatement.
- Report changes and validation; do not paste whole files.
- Prefer concise bullets and cite `file:line`.
