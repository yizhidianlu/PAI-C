"""Figure pipeline tests (Phase 1).

We mock the image backend (no real API hits) and the LLM client so the
suite stays offline. The planner / prompt-synth call sites use the same
``complete_json`` mock pattern as the rest of the test suite.
"""

from __future__ import annotations

from typing import Any

import pytest

from paic.images.backend import (
    GeneratedImage,
    ImageBackendUnavailable,
    OpenAICompatibleImageBackend,
)
from paic.images.planner import FigureSlot, _FigurePlanOutput, _FigureSlotOut
from paic.images.prompt import _ImagePromptOutput
from paic.images.storage import (
    figure_latex_snippet,
    latest_version,
    list_versions,
    load_meta,
    next_version_label,
    save_version,
)
from paic.mcp_server.tools import figure as figure_tools
from paic.mcp_server.tools.workspace import workspace_init


# --------------------------------------------------------------- fakes
class _FakeImageBackend:
    """Minimal stand-in for OpenAICompatibleImageBackend.

    Records calls so tests can assert on them without spinning a real
    client. ``model`` defaults to gpt-image-1 (so edit/variant report as
    supported); override via constructor for the dall-e-3 path.
    """

    def __init__(self, model: str = "gpt-image-1"):
        self.model = model
        self.generate_calls: list[dict[str, Any]] = []
        self.edit_calls: list[dict[str, Any]] = []
        self.variant_calls: list[dict[str, Any]] = []

    @property
    def supports_edit(self) -> bool:
        return self.model in {"gpt-image-1", "dall-e-2"}

    @property
    def supports_variant(self) -> bool:
        return self.model in {"dall-e-2"}

    def generate(self, prompt, *, n=1, size=None, quality=None):
        self.generate_calls.append({"prompt": prompt, "n": n, "size": size})
        return [GeneratedImage(png_bytes=f"GEN:{prompt}:{i}".encode()) for i in range(n)]

    def edit(self, image_bytes, prompt, *, mask_bytes=None, size=None):
        self.edit_calls.append({"image_bytes": image_bytes, "prompt": prompt})
        return GeneratedImage(png_bytes=b"EDIT:" + prompt.encode())

    def variant(self, image_bytes, *, n=2, size=None, prompt_hint=None):
        self.variant_calls.append({"image_bytes": image_bytes, "n": n})
        return [GeneratedImage(png_bytes=f"VAR:{i}".encode()) for i in range(n)]


class _StubLLM:
    """Returns canned JSON for figure_plan / figure_prompt nodes."""

    model = "stub-figure-llm"

    def __init__(
        self,
        plan_slots: list[dict] | None = None,
        prompt_text: str = "Clean illustration of method",
    ):
        self.plan_slots = plan_slots or []
        self.prompt_text = prompt_text
        self.calls: list[dict[str, Any]] = []

    def complete_json(self, *, system, user, schema, max_tokens=4096, temperature=0.0, node=None):
        self.calls.append({"node": node, "schema": schema.__name__})
        if schema is _FigurePlanOutput:
            return _FigurePlanOutput(
                slots=[_FigureSlotOut(**s) for s in self.plan_slots]
            )
        if schema is _ImagePromptOutput:
            return _ImagePromptOutput(prompt=self.prompt_text)
        raise AssertionError(f"unexpected schema {schema}")


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic"))
    from paic.config import reset_config_cache

    reset_config_cache()
    p = tmp_path / "p"
    workspace_init(p)
    return p


# --------------------------------------------------------------- storage
def test_storage_versions_increment(project):
    from paic.workspace.paths import resolve_project

    paths = resolve_project(str(project))
    label1, _ = save_version(
        paths, "teaser", b"png-bytes-1", kind="generate",
        prompt="prompt", model="gpt-image-1",
    )
    label2, _ = save_version(
        paths, "teaser", b"png-bytes-2", kind="edit",
        prompt="darker", model="gpt-image-1", parent_version=label1,
    )
    label3, _ = save_version(
        paths, "teaser", b"png-bytes-3", kind="variant",
        prompt="(variant)", model="gpt-image-1", parent_version=label1,
    )
    assert label1 == "v1"
    assert label2 == "v2_edit"
    assert label3 == "v3_variant"
    assert latest_version(paths, "teaser") == "v3_variant"
    assert list_versions(paths, "teaser") == ["v1", "v2_edit", "v3_variant"]
    meta = load_meta(paths, "teaser")
    assert len(meta["versions"]) == 3
    assert meta["versions"][1]["parent_version"] == "v1"
    assert meta["versions"][2]["parent_version"] == "v1"


def test_next_version_label_kinds(project):
    from paic.workspace.paths import resolve_project

    paths = resolve_project(str(project))
    assert next_version_label(paths, "teaser", "generate") == "v1"
    save_version(paths, "teaser", b"a", kind="generate", prompt="p", model="m")
    assert next_version_label(paths, "teaser", "generate") == "v2"
    assert next_version_label(paths, "teaser", "edit") == "v2_edit"
    assert next_version_label(paths, "teaser", "variant") == "v2_variant"


def test_latex_snippet_contains_path_and_label():
    snippet = figure_latex_snippet("teaser", "v1", caption="Our method overview.")
    assert r"\includegraphics[width=0.8\linewidth]{figures/teaser/v1.png}" in snippet
    assert r"\caption{Our method overview.}" in snippet
    assert r"\label{fig:teaser}" in snippet


# --------------------------------------------------------------- backend caps
def test_backend_capability_matrix():
    from paic.config import ProviderImagesConfig

    gpt = OpenAICompatibleImageBackend(
        ProviderImagesConfig(enabled=True, model="gpt-image-1"), api_key="x"
    )
    assert gpt.supports_edit is True
    assert gpt.supports_variant is False  # gpt-image-1 has no native /variations

    de3 = OpenAICompatibleImageBackend(
        ProviderImagesConfig(enabled=True, model="dall-e-3"), api_key="x"
    )
    assert de3.supports_edit is False
    assert de3.supports_variant is False

    de2 = OpenAICompatibleImageBackend(
        ProviderImagesConfig(enabled=True, model="dall-e-2"), api_key="x"
    )
    assert de2.supports_edit is True
    assert de2.supports_variant is True


def test_backend_supports_gpt_image_2_family():
    """gpt-image-2 / relay-suffixed aliases all pass edit (model is trusted)."""
    from paic.config import ProviderImagesConfig

    for model in ("gpt-image-2", "gpt-image-2-4k", "gpt-image-2-hd", "gpt-image-2-pro-1024"):
        b = OpenAICompatibleImageBackend(
            ProviderImagesConfig(enabled=True, model=model), api_key="x"
        )
        assert b.supports_edit is True, f"{model} should support edit"
        # No native /variations on the gpt-image-* family — variant() falls back
        # to edit() with an "alternative variation" prompt.
        assert b.supports_variant is False, f"{model} should not claim native variant"


def test_backend_unknown_model_allows_edit():
    """Unknown models are trusted (no whitelist) — relay/API decides at runtime.

    Matches the LLM provider behavior: any model name configured by the user
    is passed through. Only known-bad models (`dall-e-3`) are refused upfront.
    """
    from paic.config import ProviderImagesConfig

    b = OpenAICompatibleImageBackend(
        ProviderImagesConfig(enabled=True, model="some-future-model"), api_key="x"
    )
    assert b.supports_edit is True
    # /variations endpoint stays dall-e-2-only; everything else falls back to edit.
    assert b.supports_variant is False


def test_image_config_inherits_from_named_provider(tmp_path, monkeypatch):
    """providers.images can reference a named LLM profile to inherit
    model / api_key_env / base_url — keeps figure config consistent with
    the LLM provider grammar (no separate model whitelist either)."""
    import yaml

    from paic.config import load_config, reset_config_cache

    cfg_yaml = tmp_path / "config.yaml"
    cfg_yaml.write_text(
        yaml.safe_dump({
            "providers": {
                "openai_img": {
                    "kind": "openai",
                    "mode": "compatible",
                    "model": "gpt-image-2",
                    "api_key_env": "MYTOKEN_API_KEY",
                    "base_url": "https://relay.example/v1",
                },
                "images": {
                    "enabled": True,
                    "provider": "openai_img",
                },
            },
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("PAIC_HOME", str(tmp_path))
    reset_config_cache()
    cfg = load_config()
    img = cfg.providers_images
    assert img.enabled is True
    assert img.model == "gpt-image-2"  # inherited
    assert img.api_key_env == "MYTOKEN_API_KEY"  # inherited
    assert img.base_url == "https://relay.example/v1"  # inherited
    assert img.provider == "openai_img"


def test_image_config_explicit_overrides_named_provider(tmp_path, monkeypatch):
    """Explicit fields on providers.images take precedence over inherited."""
    import yaml

    from paic.config import load_config, reset_config_cache

    cfg_yaml = tmp_path / "config.yaml"
    cfg_yaml.write_text(
        yaml.safe_dump({
            "providers": {
                "openai_img": {
                    "kind": "openai",
                    "model": "gpt-image-2",
                    "api_key_env": "MYTOKEN_API_KEY",
                    "base_url": "https://relay.example/v1",
                },
                "images": {
                    "enabled": True,
                    "provider": "openai_img",
                    "model": "dall-e-2",  # override
                },
            },
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("PAIC_HOME", str(tmp_path))
    reset_config_cache()
    cfg = load_config()
    img = cfg.providers_images
    assert img.model == "dall-e-2"
    assert img.api_key_env == "MYTOKEN_API_KEY"  # still inherited
    assert img.base_url == "https://relay.example/v1"  # still inherited


# --------------------------------------------------------------- plan tool
def test_figure_plan_writes_yaml_and_returns_slots(project):
    llm = _StubLLM(plan_slots=[
        {
            "slot": "teaser",
            "kind": "teaser",
            "section_hint": "intro",
            "position_hint": "page 1",
            "scene_description": "Abstract conceptual scene of long-context attention",
            "caption_hint": "Overview of long-context attention.",
            "rationale": "Helps reader grasp the core idea quickly.",
        },
        {
            "slot": "Domain Example!",   # forces slug normalization
            "kind": "domain",
            "section_hint": "method",
            "position_hint": "section 3",
            "scene_description": "MRI scan illustration",
            "caption_hint": "MRI domain.",
            "rationale": "Visual context for clinical readers.",
        },
    ])

    out = figure_tools.figure_plan(str(project), max_figures=4, llm=llm)
    assert out["slot_count"] == 2
    assert out["slots"][0]["slot"] == "teaser"
    assert out["slots"][1]["slot"] == "domain_example"  # slugified
    assert (project / ".paic/figures/_plan.yaml").is_file()
    assert any(c["node"] == "figure_plan" for c in llm.calls)


def test_figure_plan_refuses_to_clobber(project):
    llm = _StubLLM(plan_slots=[
        {
            "slot": "teaser",
            "kind": "teaser",
            "section_hint": "intro",
            "position_hint": "p1",
            "scene_description": "x",
            "caption_hint": "x",
            "rationale": "x",
        }
    ])
    figure_tools.figure_plan(str(project), llm=llm)
    again = figure_tools.figure_plan(str(project), llm=llm)
    assert again["error"] == "plan_exists"
    forced = figure_tools.figure_plan(str(project), overwrite=True, llm=llm)
    assert forced.get("error") is None


# --------------------------------------------------------------- generate tool
def test_figure_generate_creates_v1_and_meta(project):
    llm = _StubLLM(
        plan_slots=[{
            "slot": "teaser", "kind": "teaser", "section_hint": "intro",
            "position_hint": "p1", "scene_description": "Concept of X",
            "caption_hint": "Cap.", "rationale": "r",
        }],
        prompt_text="Clean white-background illustration of concept X",
    )
    figure_tools.figure_plan(str(project), llm=llm)
    backend = _FakeImageBackend()
    out = figure_tools.figure_generate(
        str(project), "teaser", llm=llm, backend=backend
    )
    assert out["slot"] == "teaser"
    assert out["version"] == "v1"
    assert out["png_path"].endswith("v1.png")
    assert "Cap." in out["latex_snippet"]
    # backend got the synthesized image prompt
    assert "concept X" in backend.generate_calls[0]["prompt"]
    assert (project / ".paic/figures/teaser/v1.png").is_file()
    meta = (project / ".paic/figures/teaser/meta.yaml").read_text(encoding="utf-8")
    assert "gpt-image-1" in meta


def test_figure_generate_requires_plan_unless_free_slot(project):
    backend = _FakeImageBackend()
    out = figure_tools.figure_generate(
        str(project), "teaser", backend=backend, llm=_StubLLM()
    )
    assert out["error"] == "slot_not_in_plan"


def test_figure_generate_free_slot_requires_description(project):
    backend = _FakeImageBackend()
    out = figure_tools.figure_generate(
        str(project), "adhoc", free_slot=True, backend=backend, llm=_StubLLM()
    )
    assert out["error"] == "description_required"


def test_figure_generate_free_slot_with_description_works(project):
    backend = _FakeImageBackend()
    out = figure_tools.figure_generate(
        str(project),
        "adhoc",
        free_slot=True,
        description="A satellite image of farmland",
        backend=backend,
        llm=_StubLLM(prompt_text="Clean satellite illustration"),
    )
    assert out.get("error") is None
    assert out["version"] == "v1"


# --------------------------------------------------------------- edit tool
def test_figure_edit_bumps_version_and_records_parent(project):
    llm = _StubLLM(plan_slots=[{
        "slot": "teaser", "kind": "teaser", "section_hint": "intro",
        "position_hint": "p1", "scene_description": "x",
        "caption_hint": "Cap.", "rationale": "r",
    }])
    figure_tools.figure_plan(str(project), llm=llm)
    backend = _FakeImageBackend()
    figure_tools.figure_generate(str(project), "teaser", llm=llm, backend=backend)
    out = figure_tools.figure_edit(
        str(project), "teaser", "make it darker", backend=backend
    )
    assert out["version"] == "v2_edit"
    assert out["parent_version"] == "v1"
    assert (project / ".paic/figures/teaser/v2_edit.png").is_file()


def test_figure_edit_refuses_when_no_existing_version(project):
    backend = _FakeImageBackend()
    out = figure_tools.figure_edit(
        str(project), "teaser", "darker", backend=backend
    )
    assert out["error"] == "no_existing_version"


def test_figure_edit_unsupported_model_returns_clear_error(project):
    llm = _StubLLM(plan_slots=[{
        "slot": "teaser", "kind": "teaser", "section_hint": "intro",
        "position_hint": "p1", "scene_description": "x",
        "caption_hint": "x", "rationale": "x",
    }])
    figure_tools.figure_plan(str(project), llm=llm)
    backend = _FakeImageBackend()
    figure_tools.figure_generate(str(project), "teaser", llm=llm, backend=backend)
    de3_backend = _FakeImageBackend(model="dall-e-3")
    out = figure_tools.figure_edit(
        str(project), "teaser", "darker", backend=de3_backend
    )
    assert out["error"] == "edit_not_supported"
    assert out["model"] == "dall-e-3"


# --------------------------------------------------------------- variant tool
def test_figure_variant_uses_edit_fallback_for_gpt_image_1(project):
    llm = _StubLLM(plan_slots=[{
        "slot": "teaser", "kind": "teaser", "section_hint": "intro",
        "position_hint": "p1", "scene_description": "x",
        "caption_hint": "Cap.", "rationale": "r",
    }])
    figure_tools.figure_plan(str(project), llm=llm)
    backend = _FakeImageBackend()  # gpt-image-1 → no native variant
    figure_tools.figure_generate(str(project), "teaser", llm=llm, backend=backend)
    out = figure_tools.figure_variant(
        str(project), "teaser", n=2, backend=backend
    )
    assert out["parent_version"] == "v1"
    assert len(out["versions"]) == 2
    assert out["versions"][0]["version"] == "v2_variant"
    assert out["versions"][1]["version"] == "v3_variant"


# --------------------------------------------------------------- list tool
def test_figure_list_reports_plan_and_versions(project):
    llm = _StubLLM(plan_slots=[{
        "slot": "teaser", "kind": "teaser", "section_hint": "intro",
        "position_hint": "p1", "scene_description": "x",
        "caption_hint": "Cap.", "rationale": "r",
    }])
    figure_tools.figure_plan(str(project), llm=llm)
    backend = _FakeImageBackend()
    figure_tools.figure_generate(str(project), "teaser", llm=llm, backend=backend)
    out = figure_tools.figure_list(str(project))
    assert out["plan_id"] is not None
    assert out["slots"][0]["slot"] == "teaser"
    assert out["slots"][0]["version_count"] == 1
    assert out["slots"][0]["latest_version"] == "v1"


# --------------------------------------------------------------- backend disabled
def test_figure_generate_short_circuits_when_disabled(project, monkeypatch):
    """Without providers.images.enabled, the generate tool surfaces a clear error."""
    llm = _StubLLM(plan_slots=[{
        "slot": "teaser", "kind": "teaser", "section_hint": "intro",
        "position_hint": "p1", "scene_description": "x",
        "caption_hint": "x", "rationale": "x",
    }])
    figure_tools.figure_plan(str(project), llm=llm)
    # No backend= passed → tool reaches into get_default_image_backend(),
    # which sees enabled=false in the default config and raises.
    out = figure_tools.figure_generate(str(project), "teaser", llm=llm)
    assert out["error"] == "images_disabled"
    assert "providers.images" in out["hint"]


# --------------------------------------------------------------- doctor row
def test_doctor_emits_images_row_when_enabled(tmp_path, monkeypatch):
    import yaml as _yaml

    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    home = tmp_path / ".paic"
    home.mkdir()
    (home / "config.yaml").write_text(
        _yaml.safe_dump({
            "providers": {
                "images": {
                    "enabled": True,
                    "model": "gpt-image-1",
                    "base_url": "https://example.relay/v1",
                    "api_key_env": "OPENAI_API_KEY",
                },
            },
        }),
        encoding="utf-8",
    )
    from paic.config import reset_config_cache
    from paic.doctor import run_all

    reset_config_cache()
    rows = [c for c in run_all() if c.name == "images backend"]
    assert len(rows) == 1
    assert rows[0].severity == "ok"
    assert "gpt-image-1" in rows[0].message


def test_doctor_warns_on_dall_e_3(tmp_path, monkeypatch):
    import yaml as _yaml

    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    home = tmp_path / ".paic"
    home.mkdir()
    (home / "config.yaml").write_text(
        _yaml.safe_dump({
            "providers": {
                "images": {
                    "enabled": True,
                    "model": "dall-e-3",
                    "api_key_env": "OPENAI_API_KEY",
                },
            },
        }),
        encoding="utf-8",
    )
    from paic.config import reset_config_cache
    from paic.doctor import run_all

    reset_config_cache()
    row = next(c for c in run_all() if c.name == "images backend")
    assert row.severity == "warn"
    assert "edit/variant" in row.message


def test_doctor_omits_images_row_when_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
    from paic.config import reset_config_cache
    from paic.doctor import run_all

    reset_config_cache()
    assert not any(c.name == "images backend" for c in run_all())
