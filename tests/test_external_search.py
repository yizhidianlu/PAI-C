"""External-search (paper-search-mcp) infrastructure tests — §18."""

from __future__ import annotations

import pytest
import yaml as _yaml

from paic.config import (
    DEFAULT_EXTERNAL_SEARCH_MAX_RESULTS,
    DEFAULT_EXTERNAL_SEARCH_PRESET,
    DOMAIN_PRESETS,
    ProviderExternalSearchConfig,
    load_config,
    reset_config_cache,
)
from paic.mcp_server.tools.pacing import PACE_CAP_SECONDS, search_pace_run
from paic.mcp_server.tools.strategy import search_strategy_run
from paic.mcp_server.tools.workspace import workspace_init
from paic.workspace.store import load_yaml


# ---------------------------------------------------------------- config tests
def test_config_external_search_disabled_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic"))
    reset_config_cache()
    cfg = load_config()
    assert cfg.providers_external_search.enabled is False
    assert cfg.providers_external_search.default_preset == DEFAULT_EXTERNAL_SEARCH_PRESET
    assert (
        cfg.providers_external_search.max_results_per_platform
        == DEFAULT_EXTERNAL_SEARCH_MAX_RESULTS
    )
    # Default pacing dict should contain the well-known platforms.
    assert "pubmed" in cfg.providers_external_search.inter_call_delay_sec
    assert "biorxiv" in cfg.providers_external_search.inter_call_delay_sec


def test_config_external_search_enabled_with_custom_pacing(tmp_path, monkeypatch):
    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic"))
    home = tmp_path / ".paic"
    home.mkdir()
    (home / "config.yaml").write_text(
        _yaml.safe_dump(
            {
                "providers": {
                    "external_search": {
                        "enabled": True,
                        "default_preset": "biomed",
                        "max_results_per_platform": 5,
                        "inter_call_delay_sec": {
                            "pubmed": 0.6,
                            "biorxiv": 2.5,
                        },
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    reset_config_cache()
    cfg = load_config()
    ext = cfg.providers_external_search
    assert ext.enabled is True
    assert ext.default_preset == "biomed"
    assert ext.max_results_per_platform == 5
    # User overrides applied:
    assert ext.delay_for("pubmed") == 0.6
    assert ext.delay_for("biorxiv") == 2.5
    # Built-in defaults preserved for platforms the user didn't override:
    assert ext.delay_for("openalex") == pytest.approx(0.1, rel=1e-3)


def test_config_unknown_preset_falls_back(tmp_path, monkeypatch):
    """Garbage default_preset → fall back to the built-in default, don't crash."""
    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic"))
    home = tmp_path / ".paic"
    home.mkdir()
    (home / "config.yaml").write_text(
        _yaml.safe_dump(
            {"providers": {"external_search": {"default_preset": "not-a-real-preset"}}}
        ),
        encoding="utf-8",
    )
    reset_config_cache()
    cfg = load_config()
    assert cfg.providers_external_search.default_preset == DEFAULT_EXTERNAL_SEARCH_PRESET


def test_domain_presets_table_has_six_entries():
    expected = {
        "cs_ml", "biomed", "physics_math",
        "econ_social", "engineering", "interdisciplinary",
    }
    assert set(DOMAIN_PRESETS) == expected
    # Each preset should be non-empty.
    for name, plats in DOMAIN_PRESETS.items():
        assert isinstance(plats, list) and plats, f"empty preset: {name}"


def test_provider_external_search_delay_for_unknown_falls_back():
    cfg = ProviderExternalSearchConfig()
    assert cfg.delay_for("never_heard_of_this_platform") == 1.0


# ---------------------------------------------------------------- pace tool
def test_search_pace_default_reads_cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic"))
    reset_config_cache()
    sleeps: list[float] = []
    monkeypatch.setattr(
        "paic.mcp_server.tools.pacing.time.sleep", lambda s: sleeps.append(s)
    )
    out = search_pace_run("pubmed")
    # built-in default for pubmed = 0.4
    assert out["slept_sec"] == pytest.approx(0.4)
    assert out["platform"] == "pubmed"
    assert out["default_used"] is True
    assert sleeps == [pytest.approx(0.4)]


def test_search_pace_unknown_platform_uses_one_second(tmp_path, monkeypatch):
    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic"))
    reset_config_cache()
    sleeps: list[float] = []
    monkeypatch.setattr(
        "paic.mcp_server.tools.pacing.time.sleep", lambda s: sleeps.append(s)
    )
    out = search_pace_run("never_heard_of_it")
    assert out["slept_sec"] == 1.0
    assert sleeps == [1.0]


def test_search_pace_explicit_seconds(tmp_path, monkeypatch):
    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic"))
    reset_config_cache()
    sleeps: list[float] = []
    monkeypatch.setattr(
        "paic.mcp_server.tools.pacing.time.sleep", lambda s: sleeps.append(s)
    )
    out = search_pace_run("pubmed", seconds=2.0)
    assert out["slept_sec"] == 2.0
    assert out["default_used"] is False
    assert sleeps == [2.0]


def test_search_pace_zero_no_sleep(tmp_path, monkeypatch):
    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic"))
    reset_config_cache()
    sleep_called: list[float] = []
    monkeypatch.setattr(
        "paic.mcp_server.tools.pacing.time.sleep",
        lambda s: sleep_called.append(s),
    )
    result = search_pace_run("biorxiv", seconds=0)
    assert result["slept_sec"] == 0
    assert sleep_called == []


def test_search_pace_negative_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic"))
    reset_config_cache()
    with pytest.raises(ValueError, match="non-negative"):
        search_pace_run("pubmed", seconds=-0.5)


def test_search_pace_over_cap_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic"))
    reset_config_cache()
    with pytest.raises(ValueError, match="exceeds cap"):
        search_pace_run("pubmed", seconds=PACE_CAP_SECONDS + 0.1)


def test_search_pace_empty_platform_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic"))
    reset_config_cache()
    with pytest.raises(ValueError, match="non-empty"):
        search_pace_run("", seconds=1.0)


def test_search_pace_skip_sleep_flag(tmp_path, monkeypatch):
    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic"))
    reset_config_cache()
    sleep_called: list[float] = []
    monkeypatch.setattr(
        "paic.mcp_server.tools.pacing.time.sleep",
        lambda s: sleep_called.append(s),
    )
    result = search_pace_run("pubmed", seconds=2.0, _skip_sleep=True)
    assert result["slept_sec"] == 2.0
    assert sleep_called == []


# ---------------------------------------------------------------- workspace_init
def test_workspace_init_with_preset_writes_platforms(tmp_path):
    project = tmp_path / "biomed_project"
    result = workspace_init(project, title="Test", domain_preset="biomed")
    assert result["domain_preset"] == "biomed"
    plats = result["platforms"]
    # arxiv + semantic_scholar always prepended:
    assert plats[0] == "arxiv"
    assert "semantic_scholar" in plats
    # biomed-specific platforms present:
    assert "pubmed" in plats
    assert "biorxiv" in plats
    # And persisted to disk:
    on_disk = load_yaml(project / ".paic" / "project.yaml")
    assert on_disk["domain_preset"] == "biomed"
    assert on_disk["platforms"] == plats


def test_workspace_init_platforms_override_marks_custom(tmp_path):
    project = tmp_path / "custom_project"
    result = workspace_init(
        project, platforms_override=["pubmed", "openalex"]
    )
    assert result["domain_preset"] == "custom"
    plats = result["platforms"]
    # Legacy platforms always at the head:
    assert plats[:2] == ["arxiv", "semantic_scholar"]
    # User platforms preserved in order:
    assert "pubmed" in plats
    assert "openalex" in plats
    # No duplicates:
    assert len(plats) == len(set(plats))


def test_workspace_init_platforms_override_dedupes_legacy(tmp_path):
    """User listing arxiv explicitly shouldn't produce duplicates."""
    project = tmp_path / "dedupe_project"
    result = workspace_init(
        project, platforms_override=["arxiv", "pubmed", "arxiv"]
    )
    plats = result["platforms"]
    assert plats.count("arxiv") == 1
    assert plats.count("semantic_scholar") == 1


def test_workspace_init_no_preset_omits_platform_fields(tmp_path):
    """Calling without preset/override leaves project.yaml without platform fields."""
    project = tmp_path / "plain_project"
    result = workspace_init(project, title="Plain")
    assert result.get("domain_preset") is None
    assert result.get("platforms") is None
    on_disk = load_yaml(project / ".paic" / "project.yaml")
    assert "domain_preset" not in on_disk
    assert "platforms" not in on_disk


def test_workspace_init_preset_idempotent_update(tmp_path):
    """Re-running with a different preset updates the recorded value."""
    project = tmp_path / "p"
    workspace_init(project, domain_preset="cs_ml")
    workspace_init(project, domain_preset="biomed")
    on_disk = load_yaml(project / ".paic" / "project.yaml")
    assert on_disk["domain_preset"] == "biomed"
    assert "pubmed" in on_disk["platforms"]


# ---------------------------------------------------------------- strategy tool
def test_search_strategy_disabled_returns_legacy(tmp_path, monkeypatch):
    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic"))
    reset_config_cache()
    project = tmp_path / "proj"
    workspace_init(project)  # no preset, external_search disabled globally
    out = search_strategy_run(project_dir=project)
    assert out["enabled"] is False
    assert out["platforms"] == ["arxiv", "semantic_scholar"]
    assert out["pacing"] == {}
    assert any("disabled" in w for w in out["warnings"])


def test_search_strategy_enabled_uses_project_platforms(tmp_path, monkeypatch):
    """Enabled + project has platforms list → use that list verbatim."""
    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic"))
    home = tmp_path / ".paic"
    home.mkdir()
    (home / "config.yaml").write_text(
        _yaml.safe_dump(
            {"providers": {"external_search": {"enabled": True}}}
        ),
        encoding="utf-8",
    )
    reset_config_cache()
    project = tmp_path / "proj"
    workspace_init(project, domain_preset="biomed")
    out = search_strategy_run(project_dir=project)
    assert out["enabled"] is True
    assert out["domain_preset"] == "biomed"
    assert "pubmed" in out["platforms"]
    assert "biorxiv" in out["platforms"]
    # pacing only for non-legacy platforms
    assert "arxiv" not in out["pacing"]
    assert "semantic_scholar" not in out["pacing"]
    assert out["pacing"]["pubmed"] == pytest.approx(0.4)


def test_search_strategy_filters_unavailable_keys(tmp_path, monkeypatch):
    """ieee/acm/unpaywall should be skipped with warnings when their keys are absent."""
    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic"))
    home = tmp_path / ".paic"
    home.mkdir()
    (home / "config.yaml").write_text(
        _yaml.safe_dump(
            {"providers": {"external_search": {"enabled": True}}}
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv("PAPER_SEARCH_MCP_IEEE_API_KEY", raising=False)
    monkeypatch.delenv("IEEE_API_KEY", raising=False)
    monkeypatch.delenv("PAPER_SEARCH_MCP_ACM_API_KEY", raising=False)
    monkeypatch.delenv("ACM_API_KEY", raising=False)
    monkeypatch.delenv("PAPER_SEARCH_MCP_UNPAYWALL_EMAIL", raising=False)
    monkeypatch.delenv("UNPAYWALL_EMAIL", raising=False)
    reset_config_cache()

    project = tmp_path / "proj"
    workspace_init(
        project,
        platforms_override=["pubmed", "ieee", "acm", "unpaywall"],
    )
    out = search_strategy_run(project_dir=project)
    assert "pubmed" in out["platforms"]
    assert "ieee" not in out["platforms"]
    assert "acm" not in out["platforms"]
    assert "unpaywall" not in out["platforms"]
    msgs = " ".join(out["warnings"])
    assert "ieee" in msgs and "acm" in msgs and "unpaywall" in msgs


def test_search_strategy_falls_back_to_default_preset(tmp_path, monkeypatch):
    """Project has no platforms list → use cfg.default_preset."""
    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic"))
    home = tmp_path / ".paic"
    home.mkdir()
    (home / "config.yaml").write_text(
        _yaml.safe_dump(
            {
                "providers": {
                    "external_search": {
                        "enabled": True,
                        "default_preset": "physics_math",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    reset_config_cache()
    project = tmp_path / "proj"
    workspace_init(project)  # no preset, no override
    out = search_strategy_run(project_dir=project)
    assert out["enabled"] is True
    assert out["domain_preset"] == "physics_math"
    assert "openalex" in out["platforms"]


def test_search_strategy_ieee_available_when_key_set(tmp_path, monkeypatch):
    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic"))
    monkeypatch.setenv("IEEE_API_KEY", "fake-ieee")
    home = tmp_path / ".paic"
    home.mkdir()
    (home / "config.yaml").write_text(
        _yaml.safe_dump(
            {"providers": {"external_search": {"enabled": True}}}
        ),
        encoding="utf-8",
    )
    reset_config_cache()
    project = tmp_path / "proj"
    workspace_init(project, platforms_override=["ieee", "pubmed"])
    out = search_strategy_run(project_dir=project)
    assert "ieee" in out["platforms"]


# ---------------------------------------------------------------- doctor
def test_doctor_external_search_disabled_row(tmp_path, monkeypatch):
    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
    reset_config_cache()
    from paic.doctor import run_all

    checks = run_all()
    row = next(c for c in checks if c.name == "external search")
    assert row.severity == "skip"
    assert "disabled" in row.message


def test_doctor_external_search_enabled_row(tmp_path, monkeypatch):
    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
    home = tmp_path / ".paic"
    home.mkdir()
    (home / "config.yaml").write_text(
        _yaml.safe_dump(
            {
                "providers": {
                    "external_search": {
                        "enabled": True,
                        "default_preset": "biomed",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    reset_config_cache()
    from paic.doctor import run_all

    checks = run_all()
    row = next(c for c in checks if c.name == "external search")
    assert row.severity == "ok"
    assert "preset=biomed" in row.message
    assert "enabled=true" in row.message


# ---------------------------------------------------------------- IEEE / ACM presets + credentials
def test_acm_in_cs_ml_and_engineering_presets():
    """ACM is the largest CS publisher and a primary engineering venue;
    it must show up in both presets so users get it without manual override."""
    assert "acm" in DOMAIN_PRESETS["cs_ml"]
    assert "acm" in DOMAIN_PRESETS["engineering"]


def test_ieee_remains_in_engineering_preset():
    assert "ieee" in DOMAIN_PRESETS["engineering"]


def test_doctor_warns_when_engineering_preset_missing_ieee_and_acm_keys(
    tmp_path, monkeypatch,
):
    """When external_search is enabled with engineering preset and IEEE/ACM
    keys are absent, doctor should emit per-credential WARN rows so users
    learn the gap before /paic-search silently skips them."""
    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
    for var in (
        "PAPER_SEARCH_MCP_IEEE_API_KEY", "IEEE_API_KEY",
        "PAPER_SEARCH_MCP_ACM_API_KEY", "ACM_API_KEY",
        "PAPER_SEARCH_MCP_UNPAYWALL_EMAIL", "UNPAYWALL_EMAIL",
    ):
        monkeypatch.delenv(var, raising=False)
    home = tmp_path / ".paic"
    home.mkdir()
    (home / "config.yaml").write_text(
        _yaml.safe_dump(
            {
                "providers": {
                    "external_search": {
                        "enabled": True,
                        "default_preset": "engineering",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    reset_config_cache()
    from paic.doctor import run_all

    checks = run_all()
    cred_rows = {c.name: c for c in checks if c.name.startswith("credential:")}
    # engineering preset includes ieee + acm + unpaywall (always-checked)
    for plat in ("ieee", "acm", "unpaywall"):
        row = cred_rows.get(f"credential: {plat}")
        assert row is not None, f"missing credential row for {plat}"
        assert row.severity == "warn"
        assert "not set" in row.message
        assert row.fix and "restart Claude Code" in row.fix


def test_doctor_credential_ok_when_keys_set(tmp_path, monkeypatch):
    """All three credentials present → all rows OK."""
    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
    monkeypatch.setenv("IEEE_API_KEY", "fake-ieee")
    monkeypatch.setenv("ACM_API_KEY", "fake-acm")
    monkeypatch.setenv("UNPAYWALL_EMAIL", "user@example.com")
    home = tmp_path / ".paic"
    home.mkdir()
    (home / "config.yaml").write_text(
        _yaml.safe_dump(
            {
                "providers": {
                    "external_search": {
                        "enabled": True,
                        "default_preset": "engineering",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    reset_config_cache()
    from paic.doctor import run_all

    checks = run_all()
    cred_rows = [c for c in checks if c.name.startswith("credential:")]
    assert cred_rows, "expected per-platform credential rows when external_search is on"
    assert all(c.severity == "ok" for c in cred_rows), [
        (c.name, c.severity, c.message) for c in cred_rows
    ]


def test_doctor_credential_check_silent_when_external_search_disabled(
    tmp_path, monkeypatch,
):
    """No credential rows when external_search is off — keep doctor terse."""
    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
    reset_config_cache()
    from paic.doctor import run_all

    checks = run_all()
    cred_rows = [c for c in checks if c.name.startswith("credential:")]
    assert cred_rows == []


def test_doctor_credential_check_skips_unpaywall_when_only_biomed(
    tmp_path, monkeypatch,
):
    """biomed preset doesn't list ieee/acm; unpaywall still always checked
    because download_with_fallback uses it for any DOI-only paper."""
    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
    for var in ("PAPER_SEARCH_MCP_UNPAYWALL_EMAIL", "UNPAYWALL_EMAIL"):
        monkeypatch.delenv(var, raising=False)
    home = tmp_path / ".paic"
    home.mkdir()
    (home / "config.yaml").write_text(
        _yaml.safe_dump(
            {
                "providers": {
                    "external_search": {
                        "enabled": True,
                        "default_preset": "biomed",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    reset_config_cache()
    from paic.doctor import run_all

    checks = run_all()
    cred_rows = {c.name: c for c in checks if c.name.startswith("credential:")}
    # ieee/acm not in biomed → no rows for them
    assert "credential: ieee" not in cred_rows
    assert "credential: acm" not in cred_rows
    # unpaywall always checked
    assert cred_rows["credential: unpaywall"].severity == "warn"
