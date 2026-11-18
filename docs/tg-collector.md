# Optional Telegram Collector MCP

## Optional fast search planner

With `DECISION_PROVIDER=luna`, `LUNA_SEARCH_ENABLED=true` (default), and a non-empty
`OPENAI_API_KEY`, informational requests may use Decisions to select MCP versus web,
then one `gpt-6-luna` Responses call with `reasoning.effort=low` and
`service_tier=priority` to plan up to three read-only searches. Independent searches
run concurrently through the existing scoped retrieval functions. Full message text,
attribution, reply IDs and links are retained; repetitive history JSON keys are compacted.
Gemini receives the results and composes the personalized expandable HTML report,
without a second Luna summarization call or tools that could repeat retrieval/actions.

Both stages receive the verified sender, current question/reply and recent dialogue.
Ordinary conversation bypasses the search planner. The model cannot supply SQL,
arbitrary tools, credentials, or override the trusted chat scope.

An empty/whitespace `OPENAI_API_KEY` makes **no Luna requests**: Gemini handles the
request with its normal tools. Timeouts, API failures, uncertainty and invalid plans
also retain the Gemini path. Set `LUNA_SEARCH_ENABLED=false` or `DECISION_PROVIDER=off`
to disable the planner explicitly. `priority` is requested per call; the actual tier
is logged without request contents or credentials. This is not the Browser Use gateway.

## Collector connection

The direct AGY transport exposes a lazy read-only gateway through the existing
`geminka-bot-api` server. There is no additional daemon, startup network request,
dependency, or global AGY configuration. The background summarizer still has no MCP.
The OMP transport does not acquire these tools automatically.

Enable only in the local ignored `.env`:

```dotenv
TG_COLLECTOR_MCP_ENABLED=true
TG_COLLECTOR_MCP_URL=https://your-collector.example/mcp
TG_COLLECTOR_MCP_TOKEN=your_private_token
```

Restart Geminka after changes. The default is disabled. Missing or malformed
configuration omits the gateway tools; an unavailable server returns `ok=false`
without preventing normal conversations. Remote requests are on demand and bounded
by a 40-second total timeout. HTTPS is required except on loopback for local tests.

Only the configured owner in their private chat can call the gateway. Groups,
other users and background summaries do not gain archive access. This is an
application-level tool boundary, not a claim of host filesystem secrecy from AGY.

- `collector_tools()` discovers descriptions and input schemas.
- `collector_call(tool_name, arguments)` invokes an explicitly allowed tool only
  after discovery confirms its `readOnlyHint=true` annotation.

The gateway permits the collector's 19 known read-only tools, never arbitrary SQL,
URLs or a remote write tool. Upstream errors are results, not successful reads.
Network exception text and the token are not logged. Requests allow at most 64 KB
of arguments; oversized responses ask the model to reduce its result limit.

The model-facing guide is available with
`skill_read(name="geminka", document="references/tg-collector.md")`. It covers
all tools, filtering, pagination, examples, archive limitations and media file IDs.
Keep real credentials and endpoint configuration out of Git.

For chat summaries, Geminka retains the prefetched archive data for the current
turn. If AGY ends without text after a tool failure, one isolated, tool-free
completion can format that same captured data into the report. It does not replay
the failed tool, repeat the archive query, reset user history, or bypass group
permissions. This recovery is not used after response text has already started
or when the archive selection itself failed.
