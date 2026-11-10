# Private dialogue personalization

Default conversational custom emoji come only from the tracked
`columbina_emoji_by_geminka` catalogue. Core bootstrap selection no longer inserts
the older magic emoji ID from another pack. Ordinary streaming output allows only
default IDs and IDs actually observed in this user's private conversation; unknown
custom tags fall back to their Unicode text. Explicit channel-post templates and
command-menu copy are separate from ordinary dialogue and are not rewritten here.

## Collection and counts

The main bot collects observations from incoming private messages, after metadata
extraction. `memory.db` holds auxiliary `style_usage`, `style_events` and
`style_deliveries` tables. Per-user counts distinguish custom emoji, stickers,
animations, repeated phrases and literal repeated statements (not inferred beliefs).
Emoji occurrences are counted; stickers/GIF count once per received message.
Message receipts make reprocessing idempotent. Existing global JSON counts are not
misrepresented as historical per-user counts: new exact counters start on activation.

Text extraction excludes slash commands, code, recognized blockquotes, forwarded
text and obvious contact/credential-containing sentences. Phrase extraction is
bounded to short single-word utterances and 2–5-word fragments, with at most twelve candidates per fragment length
per sentence; literal statement candidates have 4–40 words. The vocabulary is
capped at 1000 entries per user. This is a best-effort style heuristic, not a
guarantee that all sensitive information or every meaningful thesis is recognized.
Only expressions with count >= 2 enter the compact context, with redundant shorter
fragments removed. Original capitalization is retained in displayed examples.

Debug disables collection. `/forget confirm` clears these tables for that user,
including the mixing cycle; the shared recognized pack catalogue is not deleted.
Read-only MCP attaches without migrations and only reads preference counts.

## Selection and delivery

`asset_search(query, type="emoji|sticker|gif|all", source="auto|default|user|all")`
supports source filtering and frequency boosts among relevant BM25 candidates.
Auto prefers user assets on the second and fourth reply of each five completed
private replies; duplicate receipts do not advance the cycle twice. Batching advances
the cycle once per final batch reply, not once per constituent input message.
When no personal candidate is relevant, auto falls back to defaults. Thus 40% is a
target for preferred reply source, not a forced exact percentage of all emoji/media
tokens. Explicit source requests override the automatic preference. No media is
injected solely to hit a quota; the model still decides whether media is appropriate.

`personalization_profile()` returns top private favourites and repeated expressions
with counts. Group calls cannot read this profile or personal search results. GIFs
are replayed through `<tg-gif file-id="..."/>` only if this user's private observations
contain that ID. Unknown GIF contents are not visually inferred from a filename.
Delivery control tags are hidden from text previews and executed only after a stable
final batch. Repeated GIF tags in one reply do not trigger duplicate attempts.

FTS content signatures exclude usage counts, so a frequency-only update does not
require rebuilding the whole index. Preference hints are dynamic turn context,
not changes to the frozen emoji bootstrap. Raw private profiles are never committed.
