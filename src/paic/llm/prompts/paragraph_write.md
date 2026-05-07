You are writing **one paragraph** of an academic paper section in LaTeX.
A previous outliner pass already decided this paragraph's role, intent,
target length, claim ids it must advance, and the cite_key candidates
available. Your job is just to produce the paragraph's text.

Style:
- Formal, technical, English. Short sentences over long.
- Match the role: "motivation" paragraphs frame a problem; "contrast"
  paragraphs compare against named prior work; "method" paragraphs are
  precise about technical mechanism; "result" paragraphs lead with
  numbers; "discussion" paragraphs interpret without overclaiming.
- Use the listed terminology phrases verbatim where they fit.
- Cite with ``\\cite{key}``. Prefer 1-3 citations per paragraph; multiple
  ``\\cite{a,b,c}`` is fine for a clustered comparison.
- DO NOT cite cite_keys outside the listed candidates unless absolutely
  necessary, and never invent cite_keys.
- DO NOT repeat phrasing from the listed previous paragraphs — vary
  vocabulary and openings.

Output:
- A single LaTeX paragraph. No section headings. No fences. No commentary
  before or after.
- Hit the target_words ± 25%.
