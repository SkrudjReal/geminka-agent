# Optional Telegram Collector MCP

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
