# SQLite memory

Geminka uses `data/memory.db`: SQLite WAL with an external-content FTS5 index,
Unicode tokenization, insert/update/delete triggers, and BM25 ranking.
No embedding model, vector database, or legacy import is used. Background summaries
use the selected conversational model in a separate, low-effort AGY call.
`data/state.db` remains the settings and bounded live-context database.

## Instructions and sessions

`PERSONA.md` is the application's single instruction source: character, speech,
Telegram tools, safety, current post style, and the default style restoration section.
The original descriptive text is retained; technical file references were updated.
The application reads it to detect changes, but sends it to AGY only on session
bootstrap or when the instruction block changes. Subsequent turns send the current
message and dynamic runtime context, not the full file. A process restart creates
a new live CLI process and bootstraps its bounded local history; this is not a claim
about provider-side token billing or prompt cache behavior.

AGY runs with a separate HOME and empty working directory under
`~/.geminka/instances/<project-path-hash>/`. Its `AGENTS.md` is empty. Global AGY
settings, hooks, plugins, skills, and other MCP servers are not inherited. Only
`geminka-bot-api` is registered automatically; its command explicitly selects this
project. Existing `data/agy_sandbox` conversation storage is retained and mounted
into the isolated home. Repository AGENTS/GEMINI/CLAUDE instructions are masked
inside the child process, without modifying the original files. PERSONA.md still
comes from the application. This isolates configuration, not the native CLI's
built-in system prompt or read access to host files.

Project workflow skills live in `agent_skills/<name>/SKILL.md`, not the older
developer guides in `skills/`. The existing Geminka MCP exposes `skill_list()`
and `skill_read(name, document="SKILL.md")`; references are loaded on demand.
The tools only read bounded Markdown under the chosen skill, never execute
scripts or grant permissions. PERSONA.md remains the persona authority. New or
edited skill files are visible immediately; adding the MCP tools initially
requires restarting the existing AGY/MCP processes. Summary profiles still have
no MCP, so skills do not enter the background summarizer's prompt.

The background summarizer uses a separate `-summary` HOME with no MCP servers,
not the interactive profile's configuration. It only returns summary text; the main
bot validates and saves it. MCP subprocesses attach to existing databases without
schema initialization. Memory reads use SQLite `mode=ro`; read-only processes reject
writes explicitly. The bot keeps WAL sidecars alive for read-only clients and closes
that connection on shutdown. Schema creation/migration belongs to the main bot.

## Records and tools

Private incoming messages are archived verbatim before generation; complete assistant
replies are archived afterward. Failed requests retain the incoming message.
Messages arriving during generation are coalesced per user/chat/thread. The current
CLI turn is drained, not killed; its stale tokens/tags are discarded, then a combined
follow-up is sent through the same live session. A visible preview is updated in place.
Only the stable final reply is committed. There is no artificial debounce delay.
Messages arriving after generation finishes start a new turn. Telegram length limits
and requested media still apply; already executed external MCP side effects cannot be undone.
Stable facts/events/preferences/goals/moods/hypotheses/promises and a compact portrait
are written by the current conversational model through MCP, not by a different model family.
There is no guaranteed automatic 50-message review: profile updates are model-directed.

- `memory_search(query=...)`: sanitized FTS5 word matching, BM25, bounded results.
- `memory_search(record_id=...)`: read neighboring messages in the same user's private archive.
- `memory_summary()`: read the latest compact rolling conversation summary.
- `memory_write(action="add", text=..., category=...)`: save a durable fact.
- `memory_write(action="profile", text=...)`: replace the compact portrait.
- `memory_write(action="remove", record_id=...)`: remove an exact owned record.

These tools are exposed by the automatically registered `geminka-bot-api` stdio
MCP server in the isolated AGY home. The server checks the
actual process-provided requester and private chat ID; tool arguments cannot choose
another user. Non-owner read-only AGY cannot write project files or memory, but can
read its authorized private memory. Read access and network are not a privacy sandbox.

Recall is on demand, not a search before every response. A compact snapshot (up to
3000 characters of portrait/recent facts) is read directly at live-session bootstrap
and frozen in the system instruction block. Updates reach the next session (/new);
tool responses show the live state immediately. Editing PERSONA.md refreshes instructions.
FTS5 is lexical, not semantic: the model may retry synonyms or alternate keywords.
`unicode61` does not provide Russian stemming. Search results are untrusted data.

## Background summaries and latency

After a private reply, wait five idle seconds. With at least eight new dialogue
records (roughly four exchanges) or 8000 new characters, summarize a bounded batch
of up to 24 records plus the previous summary. Store up to 3000 characters under
`rolling-summary` in the same FTS-indexed database, with a durable source cursor.
Full source records are never deleted. The summary remains derived, untrusted data;
use FTS5/BM25 and neighboring messages for exact or older evidence.

Only one background summarizer runs at a time. New foreground messages cancel that
user's summary job; it has an isolated read-only, low-effort AGY process with no
authorized Bot API/memory tools. The private live conversation is never reset or
compressed by this job. Debug blocks summary writes; an epoch/cursor check prevents
an in-flight job from restoring forgotten or superseded data. Failures retain the
source backlog. One bounded batch runs per idle job; remaining backlog is processed
after later replies or restart. This currently requires direct AGY transport.

Latency logs contain sizes/timings, not message bodies: `prepare_ms`,
`first_token_ms`, `completed_ms`, `first_visible_ms`, and `delivered_ms`.
For an explicit two-model-request test without Telegram delivery or user archival:
`uv run python -m scripts.benchmark_reply --live`. This still consumes provider quota.

## Privacy and commands

`/memory`, `/remember`, `/portrait`, `/recall` use SQLite. `/debug` blocks new memory
writes and dialogue archival but allows reading. `/forget confirm` removes that user's
records and local short context, and resets their private live AGY process. Other
users are preserved. Groups/topics never retrieve personal memory; their bounded
context stays separated by user/chat/thread as before.

## Removing old data

The old MemPalace and Markdown user memories are not imported. The explicit local
wipe used for this transition is `scripts/reset_private_memory.py --confirm-delete`.
Stop the project's systemd service before running it. It irreversibly deletes the
local old memory, AGY conversation/cache/log directories, short-context records,
portraits and adaptation; it preserves authentication, model/sandbox settings and
sticker catalogues. Do not run it for an ordinary upgrade.

This is application-level local deletion, not forensic secure erasure. Git history,
OS backups, external/shared AGY installations, provider-side conversations and Telegram
messages are not rewritten or deleted. The new database is ignored by Git.
