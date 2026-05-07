You are doing a final coherence-polish pass over a freshly assembled paper
section. The paragraphs were written one at a time; transitions may be
abrupt, short phrases may repeat, and pronouns may have orphan
references.

Your job — **only** these things:
- Fix transitions between paragraphs (one connecting clause if helpful;
  do not add a whole new sentence unless flow is genuinely broken).
- Replace repeated 3-4 word phrasings (e.g. "we propose that … we propose
  that …") with varied wording.
- Resolve orphan pronouns ("This shows …" without a clear referent → "X
  shows …").
- Keep terminology exact (the user has fixed certain phrasings).
- Preserve all ``\\cite{}`` calls verbatim — same cite_keys, same order.

Do **not**:
- Add new claims, numbers, or citations.
- Remove paragraphs or merge them aggressively.
- Translate or transliterate technical terms.
- Add commentary, analysis, or "see Figure X" inserts.

Output:
- The polished section as plain LaTeX. Maintain paragraph breaks (blank
  line between paragraphs). No fences, no commentary.
