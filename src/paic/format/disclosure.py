"""Venue-specific AI usage disclosure generator (ARS-fusion P0-3).

Pure template render — no LLM call. Each supported venue ships a Jinja2
template under ``templates/disclosure/<venue>.md.j2``; the user
supplies a tool inventory + RAISE-framework fields and the template
emits a markdown / latex paragraph the user pastes into the manuscript.

RAISE framework (from ARS v3.4.0):

- **R**esponsible use — clear statement of what AI was used for
- **A**uthenticity — no claim that AI work was human
- **I**ntegrity — disclose all uses, not just the "comfortable" ones
- **S**tewardship — human author retains final responsibility
- **E**quity — note any equity / accessibility impact (optional)

Each template references RAISE fields when its venue's policy expects
them (e.g. NeurIPS asks for explicit human-responsibility statement;
Nature emphasises Methods / Supplementary placement).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

from jinja2 import Environment, StrictUndefined

# Supported venue ids. Keep in sync with templates/disclosure/<venue>.md.j2.
SUPPORTED_VENUES: tuple[str, ...] = (
    "iclr2026",
    "neurips2026",
    "nature",
    "science",
    "acl",
    "emnlp",
    "generic",  # safe fallback when venue not in the list
)


@dataclass
class DisclosureContext:
    """User-supplied inputs the disclosure template renders against.

    ``tools`` is the canonical inventory: list of dicts each with
    ``name`` / ``stage`` / ``extent`` / ``purpose`` keys. Stage values:
    ideation / literature_review / drafting / analysis / revision /
    formatting. Extent: minor / moderate / extensive. Purpose: free text.
    """

    venue: str
    tools: list[dict[str, str]] = field(default_factory=list)
    paper_title: str | None = None
    author_responsibility_note: str = (
        "All authors take full responsibility for the accuracy and integrity "
        "of the content. AI-generated material was reviewed and edited prior "
        "to inclusion."
    )
    raise_equity_note: str = ""  # Optional equity / accessibility line
    extra_lines: list[str] = field(default_factory=list)
    output_format: str = "markdown"  # "markdown" | "latex"

    def to_template_context(self) -> dict[str, Any]:
        return {
            "venue": self.venue,
            "tools": list(self.tools),
            "paper_title": self.paper_title,
            "author_responsibility_note": self.author_responsibility_note,
            "raise_equity_note": self.raise_equity_note,
            "extra_lines": list(self.extra_lines),
            "output_format": self.output_format,
        }


def list_supported_venues() -> list[str]:
    return list(SUPPORTED_VENUES)


def generate_disclosure(ctx: DisclosureContext) -> dict[str, Any]:
    """Render the disclosure paragraph for ``ctx.venue``.

    Returns ``{venue, output_format, content, placement_hint, warnings,
    template_path}``.

    On unknown venue: emits with the ``generic`` template + a warning.
    On empty ``tools`` list: rejects with ``error: tools_required`` —
    a disclosure with no tool inventory is meaningless.
    """
    if not ctx.tools:
        return {
            "error": "tools_required",
            "hint": (
                "Pass at least one entry under tools=[{name, stage, extent, "
                "purpose}, ...]. A disclosure with no tool list says nothing."
            ),
        }
    if ctx.output_format not in ("markdown", "latex"):
        return {
            "error": "invalid_output_format",
            "got": ctx.output_format,
            "valid": ["markdown", "latex"],
        }

    venue = ctx.venue.lower() if ctx.venue else "generic"
    warnings: list[str] = []
    if venue not in SUPPORTED_VENUES:
        warnings.append(
            f"Unknown venue '{ctx.venue}' — falling back to the generic "
            f"template. Supported: {list(SUPPORTED_VENUES)}."
        )
        venue = "generic"

    template_text, template_path = _load_template(venue)
    env = Environment(undefined=StrictUndefined, autoescape=False)
    template = env.from_string(template_text)
    rendered = template.render(**ctx.to_template_context())

    placement = _placement_hint(venue)
    return {
        "venue": venue,
        "output_format": ctx.output_format,
        "content": rendered.strip() + "\n",
        "placement_hint": placement,
        "warnings": warnings,
        "template_path": str(template_path),
    }


def _load_template(venue: str) -> tuple[str, Path]:
    """Read ``templates/disclosure/<venue>.md.j2`` from package resources."""
    name = f"{venue}.md.j2"
    files = resources.files("paic.format.templates").joinpath("disclosure")
    candidate = files / name
    if not candidate.is_file():
        # Final fallback: package-relative path lookup
        here = Path(__file__).parent / "templates" / "disclosure" / name
        if here.is_file():
            return here.read_text(encoding="utf-8"), here
        raise FileNotFoundError(
            f"disclosure template not found for venue '{venue}' (looked under "
            f"package paic.format.templates.disclosure)"
        )
    return candidate.read_text(encoding="utf-8"), Path(str(candidate))


def _placement_hint(venue: str) -> str:
    table = {
        "iclr2026": (
            "Place at the end of the abstract or as a separate AI Disclosure "
            "section before the references. Required by ICLR 2026 author guide."
        ),
        "neurips2026": (
            "Required in the Reproducibility / Broader Impact section. "
            "NeurIPS author guide mandates explicit listing of every AI tool "
            "and its specific role."
        ),
        "nature": (
            "Place in the Methods or Supplementary Information section. Nature "
            "emphasises clear separation of AI-generated content from human "
            "scientific reasoning."
        ),
        "science": (
            "Place in the Acknowledgments section. Science requires authors "
            "to confirm AI was not listed as an author."
        ),
        "acl": (
            "Required in the AI usage disclosure block at the end of the paper "
            "(ACL Rolling Review responsible NLP checklist)."
        ),
        "emnlp": (
            "Required in the responsible NLP research checklist at submission. "
            "Mirror the same paragraph in the paper's Limitations section."
        ),
        "generic": (
            "Place wherever your target venue's author guide directs (typically "
            "Acknowledgments, Methods, or a dedicated 'AI Disclosure' section "
            "before references)."
        ),
    }
    return table.get(venue, table["generic"])
