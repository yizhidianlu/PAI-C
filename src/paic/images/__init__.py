"""Image generation pipeline (Phase 1) — paper figure helpers.

Wraps the OpenAI-compatible Images API (gpt-image-1 / dall-e-*) behind a
single :class:`OpenAICompatibleImageBackend`. The :mod:`paic.images.planner`
module turns paper context into a structured set of figure slots; the
:mod:`paic.images.prompt` module turns a single slot into an image-model
prompt; :mod:`paic.images.storage` handles versioned PNG + meta.yaml
persistence under ``<project>/.paic/figures/<slot>/``.

The MCP layer (``paic.mcp_server.tools.figure``) exposes plan / generate /
edit / variant / list as ``paic_figure_*`` tools driven by the
``/paic-figure`` skill.
"""

from paic.images.backend import (
    ImageBackendUnavailable,
    OpenAICompatibleImageBackend,
    get_default_image_backend,
)
from paic.images.planner import FigureSlot, plan_figures
from paic.images.prompt import synthesize_image_prompt
from paic.images.storage import (
    figure_latex_snippet,
    latest_version,
    list_versions,
    load_meta,
    next_version_label,
    save_version,
)

__all__ = [
    "FigureSlot",
    "ImageBackendUnavailable",
    "OpenAICompatibleImageBackend",
    "figure_latex_snippet",
    "get_default_image_backend",
    "latest_version",
    "list_versions",
    "load_meta",
    "next_version_label",
    "plan_figures",
    "save_version",
    "synthesize_image_prompt",
]
