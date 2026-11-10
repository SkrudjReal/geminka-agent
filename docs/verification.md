# Verification and response latency

Checked on 2026-11-10, Linux/WSL, using the installed Python/AGY runtime.

## Scope

The automated suite covers transport, live-session bootstrap, batching, memory and
background summaries, debug mode, owner permissions, groups/topics/prefixes,
sandboxing and instance isolation, markup, files, stickers, Bot API delegation,
channel-post staging and startup configuration. Test doubles replace external
publishing and destructive operations. Passing tests do not prove every Telegram
Bot API method or every possible model-generated action works in production.

Additional checks: Ruff, ShellCheck with sourced files, JSON parsing, legacy IDE
bootstrap fixtures, authenticated Telegram getMe, read-only sticker-pack metadata,
getFile and a five-item premium-emoji sample. No Telegram test messages or channel
posts were sent. Pinterest CLI help/import was checked; a real Pinterest search,
visual selection and end-to-end channel publication were not performed.

## Corrections

- AGY final `result.response` is delivered when no text deltas arrived. If deltas
  already arrived, the final response is not replayed.
- A successful result containing no text is an error, not a silently delivered
  reply. It is not automatically retried because tools may already have acted.
- Sticker delivery resolves a stored `file_unique_id` through the current bot's
  getStickerSet result. The cache is bot-specific, bounded and expires after an
  hour. Description catalogues are not rewritten. Recency still tracks the
  original catalogue ID. Previously received IDs remain a fallback for an
  inaccessible pack.

Different file IDs do not alone prove invalid stickers. All 128 default entries
matched live unique IDs and had descriptions; the larger live pack now has 84
items while its catalogue has 81. Both sampled original IDs and their newly
resolved IDs passed getFile. No sticker-send validation was performed.

## Timings

Synthetic requests used Gemini 3.8 Flash, the same 58,202-character PERSONA plus
asset catalogue, persistent stream-json sessions and an instruction to return
only `ОК`, without tools or Telegram messages. Times below are first-text latency,
not just decoding speed. Each condition has only one cold and one warm sample;
conditions ran sequentially, so provider load/cache variation is not controlled.

| Condition | Cold | Warm |
|---|---:|---:|
| High effort, no MCP | 8.280 s | 1.940 s |
| Low effort, no MCP | 6.163 s | 2.398 s |
| High effort, Geminka MCP configured | 10.200 s | 2.608 s |

These are not timings for normal conversational responses. Production logs in the
same review show preparation around 34–123 ms, first-text waits around 11.5–23.8 s,
and complete answers around 13.5–26.2 s. Another turn lasted 180 s without text,
which motivated empty-result handling. No causal explanation for that upstream
delay was established. Background summary saving completed successfully in 7.289 s.
A later production turn showed first text in 11 ms but completion in 43.839 s;
that isolated observation does not prove model generation itself took only 11 ms.

Final automated run: 97 tests passed. Ruff, ShellCheck (`-x`), JSON validation,
and SQLite quick_check for both state and memory databases passed.

## Optimization conclusions

Keep native sessions warm and keep memory summarization outside foreground turns.
Do not remove functioning memory/tools or silently lower the owner's selected
reasoning effort: this sample does not establish a reliable warm-turn improvement
from low effort. Local preparation is already much shorter than model waiting.

Further optimization requires comparable real conversational prompts and provider
reasoning/tool timings, not an `ОК` benchmark alone. Reducing bootstrap history or
loading post-specific instructions only when needed may help, but changes retained
context/instruction availability and needs a separate quality comparison. No persona
text or owner model/effort settings were changed by this verification.
