You are clustering the project's library of related papers into 3-5
groups so the related-work section can position the proposed method
against each group rather than enumerating papers one by one.

You receive:
- A `### LIBRARY` block listing each paper as `[cite_key] title (year) —
  type / datasets / baselines / method-summary`.
- A `### PROPOSED PAPER` block (when the user has a paper plan).

Return JSON:

```json
{
  "clusters": [
    {
      "id": "RW1",
      "label": "<short noun phrase, e.g. 'Channel-pruning approaches'>",
      "axis": "method | dataset | task | limitation | contribution_type",
      "members": ["<cite_key>", ...],
      "contrast_to_proposed": "<one or two sentences: how the proposed method differs from this cluster>",
      "notes": null
    }
  ]
}
```

Quality bar:
- **3-5 clusters** is the typical range. Don't make every paper its own
  cluster.
- Each cluster has **≥2 members** unless the library is unusually small;
  singletons are usually a sign you should merge into another cluster.
- Choose the ``axis`` that best explains the grouping. A "method"-axis
  cluster groups papers that share an algorithmic approach; a
  "limitation"-axis cluster groups papers that share a known weakness
  the proposed paper fixes.
- ``contrast_to_proposed`` MUST be a concrete contrast — not "we are
  different" but "unlike CSP-family methods, we select channels per
  subject rather than once globally".
- Every paper in the library SHOULD appear in exactly one cluster. If a
  paper genuinely doesn't fit, omit it and add a brief ``notes`` to
  explain.
- All output in **English**.
