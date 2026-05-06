"""LaTeX writing pipeline.

v0.1: ``filler.py`` populates a venue template (cvpr / neurips / ieee) from an
IdeaCard + ExperimentPlan + the project library.
v0.2 (later): ``polish.py`` paragraph polishing.
v0.3 (later): ``compose.py`` full draft composition.
"""

from paic.latex.filler import VENUES, fill_draft

__all__ = ["VENUES", "fill_draft"]
