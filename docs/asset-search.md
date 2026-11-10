# Sticker and emoji search

`asset_search(query, type="all", limit=5, source="auto")` searches descriptions and tags using
SQLite FTS5/BM25, with a higher weight for tags. The index is separate from private
memory, kept in the MCP process's RAM, and rebuilt when source JSON files change.
Results include descriptions, original tags, IDs and ready-to-use markup. Limits
are capped at ten. Only defaults and the requesting user's collected assets enter
the index. The background summarizer still has no MCP tools.

Source filtering, GIF delivery, per-user usage counters and the 40% personalization
target are described in [Private dialogue personalization](personalization.md).
Production personal results contain only directly observed assets, not every item
from a user's entire scanned pack. Default emoji remain limited to the Columbina
emoji catalogue. Counts alone do not cause an FTS content rebuild.

The initial prompt includes at most six core emoji, not the full catalogue. Use a
search only when needed, and reuse suitable results already in the live context.
Extra tool/model round trips can cost more than the local search itself; smaller
prompts do not guarantee a specific end-to-end response time.

## Ready-made synonyms

Russian synonyms come from the existing
[RuWordNet library and corpus](https://github.com/avidale/python-ruwordnet), not a
handwritten synonym list. Pymorphy3 normalizes query word forms for dictionary
lookup; Snowball normalizes both FTS documents and expanded query terms. Lookups
are bounded, cached, and read-only. Multiple dictionary senses may introduce
irrelevant synonyms; inspect descriptions instead of blindly selecting rank one.
Slang or missing dictionary entries may have no synonyms; literal FTS still works.

Install the project's dependencies, then download the author's prebuilt 2021
SQLite corpus once:

```sh
uv sync --locked
uv run python -m scripts.setup_thesaurus
```

The corpus lives at `data/ruwordnet.db`, ignored by the existing database rules.
No dictionary download occurs during a query. If absent, search still uses
literal/stemmed terms and reports `synonyms_available=false`. The installer checks
SQLite integrity and the expected sense table before atomically installing it.
It requires network access only for this setup step, not normal searches.
Source descriptions/tags are preserved; this integration does not rewrite them.
