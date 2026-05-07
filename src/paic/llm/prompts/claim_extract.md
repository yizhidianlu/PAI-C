You are extracting **claim-shaped sentences** from a single composed paper
section. A claim is any assertion the author makes that a reader could
verify or dispute. Your job is to surface *just* the assertions that need
support — not every sentence.

You receive a `### SECTION` name and `### TEXT` block (LaTeX source).

Return JSON:

```json
{
  "claims": [
    {
      "text": "<paraphrase the claim in one short sentence; preserve numbers verbatim>",
      "type": "novelty | comparative | factual | numeric | methodological | result",
      "status": "needs_evidence | supported | todo",
      "required_citations": ["<cite_key>", ...],
      "notes": "<optional one-line note, or null>"
    }
  ]
}
```

Type definitions:

- **novelty**: "first / new / never before" assertion. *Always* needs prior
  work to prove the gap is real.
- **comparative**: "X beats Y" / "more efficient than baseline" / "better
  than prior work". Needs cited baseline + experiment result.
- **numeric**: any specific number quoted in prose ("3.2% improvement",
  "5x faster"). Must trace to an experiment artifact.
- **factual**: a generally-known fact ("EEG signals are noisy"). At most
  needs a citation.
- **methodological**: an internal method-design choice (e.g. "we use
  cosine annealing"). Often supported by an ablation.
- **result**: a specific experimental outcome ("our method reaches 78.4%
  on BCI-IV-2a"). Must trace to experiment + (ideally) table / figure.

Status:

- **needs_evidence**: claim is in the draft but has no inline `\\cite{}`
  and no clear pointer to an experiment artifact. (Default for most.)
- **supported**: the prose includes one or more `\\cite{}` calls or
  references a result we can attribute.
- **todo**: the prose contains an explicit TODO marker (`\\todo{}` or
  `% TODO`).

Quality bar:

- **Skip filler sentences** ("In this section, we describe..."). They are
  not claims.
- **Preserve numbers verbatim** in `text`. Don't round 3.27% to 3%.
- **`required_citations`** lists cite_keys present in the prose's
  `\\cite{}` calls, plus any cite_keys you think *should* support the
  claim but don't appear yet (these come back as "missing" after
  validation).
- Output is **English** even if the section text contains some non-English.
- Return an empty `claims` list when the section is genuinely free of
  assertions (e.g. a table-of-contents-style intro paragraph).
