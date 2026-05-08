"""Format-convert + disclosure package (ARS-fusion P0-3).

Two MVP capabilities:

1. **Format conversion** — LaTeX / DOCX (via Pandoc) / PDF / Markdown +
   citation style conversion (APA 7 / Chicago / MLA / IEEE / Vancouver
   via CSL + ``pandoc --citeproc``). Pandoc is a weak dependency:
   missing pandoc → graceful degrade to instructions.

2. **Venue-specific AI usage disclosure** — Jinja2 templates for ICLR /
   NeurIPS / Nature / Science / ACL / EMNLP, fed by user-supplied tool
   inventory + RAISE-framework fields. Pure template render, no LLM.
"""

from paic.format.disclosure import (
    SUPPORTED_VENUES,
    DisclosureContext,
    generate_disclosure,
    list_supported_venues,
)
from paic.format.pandoc_bridge import (
    PANDOC_BINARY,
    PandocUnavailable,
    convert_document,
    pandoc_available,
)

__all__ = [
    "PANDOC_BINARY",
    "PandocUnavailable",
    "SUPPORTED_VENUES",
    "DisclosureContext",
    "convert_document",
    "generate_disclosure",
    "list_supported_venues",
    "pandoc_available",
]
