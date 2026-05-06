"""Prompt asset loader."""

from importlib import resources
from pathlib import Path


def load_prompt(name: str) -> str:
    """Load ``paic/llm/prompts/<name>.md`` (or .txt) as a string."""
    files = resources.files("paic.llm.prompts")
    for ext in (".md", ".txt"):
        candidate = files / f"{name}{ext}"
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8")
    # Fallback: file may exist on disk during development install
    here = Path(__file__).parent
    for ext in (".md", ".txt"):
        candidate_path = here / f"{name}{ext}"
        if candidate_path.is_file():
            return candidate_path.read_text(encoding="utf-8")
    raise FileNotFoundError(f"Prompt '{name}' not found")
