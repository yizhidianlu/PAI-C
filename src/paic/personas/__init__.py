"""5 reviewer persona prompts loaded as plain strings.

Persona ordering matters: the review graph dispatches them in this order,
and the moderator treats ``devils_advocate`` critiques as highest priority
when synthesizing the round (see ARS-fusion P1-3).
"""

from importlib import resources

PERSONA_NAMES = (
    "methodology",
    "statistics",
    "domain",
    "reviewer2",
    "devils_advocate",
)


def load_persona(name: str) -> str:
    if name not in PERSONA_NAMES:
        raise ValueError(f"Unknown persona '{name}'. Valid: {PERSONA_NAMES}")
    files = resources.files("paic.personas")
    return (files / f"{name}.md").read_text(encoding="utf-8")
