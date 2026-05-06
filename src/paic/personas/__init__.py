"""4 reviewer persona prompts loaded as plain strings."""

from importlib import resources

PERSONA_NAMES = ("methodology", "statistics", "domain", "reviewer2")


def load_persona(name: str) -> str:
    if name not in PERSONA_NAMES:
        raise ValueError(f"Unknown persona '{name}'. Valid: {PERSONA_NAMES}")
    files = resources.files("paic.personas")
    return (files / f"{name}.md").read_text(encoding="utf-8")
