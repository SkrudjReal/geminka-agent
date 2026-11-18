# Fast decisions and retrieval

Geminka keeps the selected AGY model and reasoning level. A separate optional
decision service chooses retrieval routes; it cannot change permissions, user
identity, mood, the persona, SQL, or tool arguments.

## Providers

Configure `.env`, then restart the project's service:

```dotenv
DECISION_PROVIDER=luna
OPENAI_API_KEY=your-own-key
```

Luna uses `gpt-6-luna` at `https://api.openai.com/v1/decisions` with one request
for action, data source and answer length. This is paid API access, separate
from AGY authentication. Text routing has a two-second total budget and no
automatic retries. Failed, refused or uncertain decisions fall back to the
existing pipeline. Repeated failures temporarily open a circuit breaker.

For a separately deployed Laya server:

```dotenv
DECISION_PROVIDER=laya
LAYA_BASE_URL=http://127.0.0.1:8000
LAYA_MODEL=multilingual
LAYA_API_KEY=
```

Install `laya[serve]` in a **separate environment**, start `laya-serve` according
to its documentation, and secure its listener. Geminka speaks the
`POST /v1/systemone` protocol with `state` and named `questions`. Non-loopback
servers require HTTPS. It does not install PyTorch, download weights or start
a Laya server itself. Multilingual is appropriate for Russian input; benchmark
the deployed hardware rather than relying on published GPU timings.

Laya is used for text routing and observer decisions. Pinterest pixel checks
continue through Gemini in Laya mode. In Luna mode they use image predicates,
falling back to Gemini if unavailable. `DECISION_PROVIDER=off` restores normal
retrieval and Gemini vision without a decision API dependency.

Sources: [OpenAI Decisions](https://developers.openai.com/api/docs/guides/decisions),
[Laya](https://github.com/NandhaKishorM/laya).

## Retrieval routes

- Ordinary chat retains local group context and the stored summary without
  waiting for optional remote profiling or FTS searches when unnecessary.
- History summaries use `search_messages` and the server's full cursor. Full
  message texts, attribution, dates and reply IDs are retained; bulky transport/media
  metadata is omitted. Selections up to 500,000 serialized characters go directly
  into the final model turn, without a queue of intermediate summary calls.
  Larger selections use blocks before the final persona reply.
- Analytics uses server-side statistics/top-author/word/phrase/activity tools,
  not model counting over search samples.
- Topic requests use hybrid `search`; optional bounded decision scoring reorders
  full results without dropping records or shortening their text. Ambiguous dates, other users and
  cross-chat scopes are left to the existing tools rather than guessed.
- Explicit CVE/archive/report requests cannot be downgraded to casual chat by an
  uncertain decision. Short follow-ups inherit the preceding user request, not
  facts from an assistant's potentially unsupported answer. Private topic
  searches cover archived groups/channels instead of filtering by the bot DM ID.
- Full user reports resolve an exact username or the trusted requester ID for
  self-reports, then fetch statistics, activity, words, phrases, incoming/outgoing
  replies, public message examples and (owner-private only) profile history.
  Missing sections are disclosed. Reply-partner scope never includes sender
  filters, as required by the collector. Reports use expandable HTML sections,
  concrete figures and sources rather than replacing analytics with banter.
- Explicit ISO dates and today/yesterday use `TZ` (default Europe/Warsaw).
- Automatic history reads are bounded at 5,000 messages per request and
  disclose partial selection. Dates/IDs, public-only filtering and permissions
  remain Python/server responsibilities. A failed block summary is never
  presented as a completed full-history report.

MCP uses an authenticated, lazily opened session with read-only tool discovery,
four concurrent calls, single-flight identical requests and a bounded cache.
Cache scopes include the complete connection credentials and complete tool
arguments; results are copied before group filtering. Successful ordinary
queries cache for five seconds, analytics for thirty seconds and discovery for
five minutes. Connections close after an idle interval or bot shutdown.
The revocable group broker rechecks access on every call and does not cache
responses in its untrusted client. Its upstream trusted connection can cache
data only after the broker has checked the current group capability.

SQLite retains FTS5/BM25 and WAL. Composite user/kind indexes support summaries,
history and preferences; no embedding model or additional storage service is
required. Raw messages are not deleted by these changes.

## Observability and privacy

Logs record decision, archive, retrieval, AGY queue, first-token, preparation
and total delivery timings, without prompts, archive text or API keys. A
decision router adds latency if it does not eliminate more expensive work;
compare the same scenario, warm/cold sessions and p50/p95 before making claims.

Decision requests send selected message text to the configured provider.
Luna also receives Pinterest image pixels. Local Laya keeps text decisions on
the configured server. Credentials stay in the ignored `.env`; they are not
passed to group runtimes. Unit tests disable live Decisions requests by default.
