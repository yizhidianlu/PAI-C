# Collaboration Depth Rubric (ARS-fusion P2-2)

> **Adapted from**: Wang & Zhang (2026), "AI-augmented research
> collaboration: a depth taxonomy". *International Journal of
> Educational Technology in Higher Education*, 23:11.
> DOI: 10.1186/s41239-026-00585-x

The rubric is consumed by the `collaboration_depth` agent, which is
**advisory only** — its output never blocks any /paic-pipeline
checkpoint. The intent is to give the user a mirror, not a gate.

## Why this exists

Long PAI-C pipeline runs (init → finalize spans days) have a known
failure mode: the user's vigilance drops as they get used to "say
continue, see good output". Without an observer, that pattern is
invisible until paper rejection or post-hoc reflection. The depth
rubric surfaces it during the run so the user can self-correct.

## 4 dimensions

### 1. Delegation Intensity (0-100)

How much of the substantive work has the user handed off to the AI?

| Score range | Pattern |
|---|---|
| 0-25 | User wrote / decided most things; AI mostly re-formatted |
| 26-50 | Roughly even — user drives, AI assists |
| 51-75 | AI drives; user reviews + redirects |
| 76-100 | AI does the work; user accepts most outputs |

Evidence signals:
- Number of user-supplied substantive paragraphs vs LLM-generated
- Frequency of "rewrite this" vs "good, continue"
- Whether the user supplied the idea/experiment design vs accepted LLM proposal

### 2. Cognitive Vigilance (0-100)

How critically does the user engage with intermediate outputs?

| Score range | Pattern |
|---|---|
| 0-25 | "Continue" / "looks good" / no specific objections recorded |
| 26-50 | Occasional pushback on obvious errors |
| 51-75 | Regular pushback, asks "why this baseline" / "is this novel" |
| 76-100 | Each intermediate output received specific critique |

Evidence signals:
- Count of `paic_pipeline_advance` calls with substantive `verdict` text
- Count of `revision_apply` / `revision_resolve` user-driven actions
- Presence of user rebuttals in `/paic-review` rounds

### 3. Cognitive Reallocation (0-100)

What does the user do with the time saved by delegating to AI?

| Score range | Pattern |
|---|---|
| 0-25 | Time not visibly reallocated to deeper engagement |
| 26-50 | Mixed — some deeper work observed |
| 51-75 | Visible deep-think activity (notes, follow-up questions) |
| 76-100 | Time clearly reallocated to higher-value research thinking |

Evidence signals:
- User-supplied notes / clarifications in stages following AI outputs
- Cross-stage continuity of thought (referencing prior decisions)
- Frequency of off-pipeline deep questions (e.g. "what if we tried Z instead?")

> **Caveat**: this dimension is hard to score from PAI-C dialogue
> alone. Mark as `INSUFFICIENT_EVIDENCE` when in doubt; better to
> under-claim than to flatter.

### 4. Zone Classification

Synthesize the three dimensions into one of:

- **Zone 1 (productive)**: high vigilance + meaningful reallocation,
  any delegation level. The user is using AI to amplify, not replace.
- **Zone 2 (mixed)**: any other combination not clearly in Zone 1 or 3.
  Most common; not pathological.
- **Zone 3 (concerning)**: low vigilance (< 30) AND no reallocation
  observed (< 30) AND high delegation (> 70). The user is on autopilot.

## How the observer uses this

1. Read the dialogue window for the just-completed stage (or whole
   pipeline at completion).
2. Score each dimension 0-100 with one line of evidence.
3. Assign Zone.
4. Surface the observation in the checkpoint UX as advisory only.

The observer never tells the user what to do — it surfaces the
pattern. The user can choose to slow down, push back more, or simply
accept that this paper is a delegation-heavy run.

## Skipping rules

- **MANDATORY checkpoints** (stages 6 / 9 / 10) — observer is NEVER
  invoked. Those stages are integrity-critical and the user's
  attention should be 100% on integrity verdicts, not on a depth
  meta-comment.
- **Single-SKILL invocations outside /paic-pipeline** — observer is
  not invoked. The depth pattern only makes sense across multiple
  related stages.

## Cross-model variant (P2-3 hook)

When `routing.cross_model_evaluator: true` is on, the observer can
optionally run on both backends (writer's + evaluator's) and flag any
dimension where the two assessments diverge by > 15 points. Useful as
a sanity check that the observation isn't itself a single-model blind
spot.

## Caveats

- Wang & Zhang (2026) was developed for human-AI co-writing in higher
  education contexts. STEM paper writing has different priors —
  delegation patterns that look "low vigilance" in HE contexts may be
  appropriate in STEM (e.g. accepting a clearly-correct algebra
  derivation without scrutiny).
- The observer is a **mirror**, not a verdict. The user's domain
  knowledge always trumps the rubric.

## References

- Wang, S. & Zhang, L. (2026). AI-augmented research collaboration: a
  depth taxonomy. *IJETHE*, 23:11. DOI: 10.1186/s41239-026-00585-x
- ARS v3.5.0 `collaboration_depth_agent.md` (the original
  implementation we adapted).
