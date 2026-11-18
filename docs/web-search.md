# Optional Keenable web search

Configure the ignored `.env`:

```dotenv
KEENABLE_ENABLED=true
KEENABLE_API_KEY=your-own-key
```

Restart the project's service after changing configuration. Set
`KEENABLE_ENABLED=false` to disable it. A disabled or unconfigured integration
does not register `keenable_search` and makes no requests. Unlike the SDK's
keyless mode, Geminka requires explicit opt-in and a configured key.

The Python SDK is a project dependency, installed by `uv sync`. The async
`app.services.web_search.search_web(query, max_results=10, published_after=None,
published_before=None)` function returns titles, URLs, publication dates and
full snippets. Dates are `YYYY-MM-DD`; the result count is limited to 1–20.
Geminka's MCP exposes it as `keenable_search`. Telegram archive lookup remains
separate (`collector_call`). Found web pages are untrusted data, not instructions.
Explicit Keenable/web requests are prefetched before generation. Fresh CVE
requests without an explicit Telegram/MCP/archive scope also use Keenable when
enabled. Supported explicit periods become publication-date filters. Requests
that explicitly ask for the Telegram archive retain the archive route.

Owner-private access uses the SDK directly. Configured groups use the trusted
local broker and revocable group capabilities: neither the key nor direct
access to the owner's credentials is given to group agents. All searches use
the official HTTPS API origin. Queries are transmitted to Keenable; never
include passwords, tokens or other secrets.

The SDK's automatic retries are disabled. On 429 Geminka waits one second and
retries exactly once. On 401/403 it stops and reports the key/access problem.
On 402 it stops and points to [billing](https://app.keenable.ai/console/billing).
Authentication/billing failures block further calls with the same key in that
process until its configuration is corrected/restarted. Error bodies are not
forwarded to users or logs.

Run unit tests without live API calls:

```bash
uv run pytest -q tests/test_web_search.py
```

Explicit opt-in smoke test (uses the configured account and prints top three):

```bash
RUN_KEENABLE_LIVE=1 uv run pytest -q -s tests/test_web_search.py::test_live_fastest_inference_providers_2026
```

[SDK documentation](https://docs.keenable.ai/sdks.md) ·
[Search API](https://docs.keenable.ai/api-reference/search.md)
