# Personal news brief

The board uses official publisher RSS feeds: FT Global Economy, WSJ Markets,
Federal Reserve monetary-policy releases, CoinDesk, and FT Technology. Fetches
are cached for 15 minutes, independently of the one-minute image refresh.

Only headlines matching macro, crypto or technology terms are admitted. Items
must have a publication timestamp within `max_age_h` (18 hours by default), with
five minutes of clock tolerance. Missing-date, old and future-dated items are
not relabelled as fresh. Source title/link duplicates are removed; topics are
interleaved Macro / Crypto / Tech to prevent one publisher flooding the board.
This is transparent keyword filtering, not an LLM assessment or semantic story
clustering. Tune `NEWS_TOPICS` and `NEWS_NOISE` in `server/sources.py`.

The existing admin feed selector retains legacy feeds, but their headlines must
pass the same relevance rules. A failed feed retains its last successful cached
value; the publication-age gate still applies. Source health is in news status.
FT/WSJ feed headlines do not grant access to their paywalled full articles.

## TinyFish

TinyFish is NOT enabled. No TinyFish API credential is present in the board's
service environment. The direct publisher feeds work without credentials, so
they remain the baseline rather than adding a browser-agent dependency to the
refresh loop.

For a future optional enrichment worker, use TinyFish Fetch or Agent for a
fixed allowlist of sources without usable feeds, on a slow 30–60 minute schedule.
Validate the returned article URL, actual title, publisher and original date;
then pass it through the same relevance/age/deduplication gate. Never invent an
article date or replace the baseline feed cache on an empty/error response.
Keep the key in the private service environment, not Git or frontend JavaScript.
Verify a real authenticated response before deploying any adapter.

Official documentation checked: `https://docs.tinyfish.ai/llms.txt`,
`https://docs.tinyfish.ai/fetch-api/reference.md`, and
`https://docs.tinyfish.ai/authentication.md`.
