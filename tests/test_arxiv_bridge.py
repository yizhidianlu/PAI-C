"""arxiv_bridge multi-root probing tests — Fix 1."""

from __future__ import annotations

from pathlib import Path

from paic.config import (
    Config,
    ProviderAnthropicConfig,
    ProviderArxivConfig,
    ProviderExternalSearchConfig,
    ProviderImagesConfig,
    ProviderSemanticScholarConfig,
    RoutingConfig,
)
from paic.sources.arxiv_bridge import (
    find_local_markdown,
    iter_storage_roots,
    read_local_markdown,
)


def _cfg(*roots: Path) -> Config:
    return Config(
        global_dir=Path("/tmp/paic"),
        default_model="claude-opus-4-7",
        arxiv_mcp_storage_paths=tuple(roots),
        semantic_scholar_api_key=None,
        anthropic_api_key=None,
        openai_api_key=None,
        providers_anthropic=ProviderAnthropicConfig(),
        providers_openai=None,
        providers_s2=ProviderSemanticScholarConfig(),
        providers_arxiv=ProviderArxivConfig(),
        providers_external_search=ProviderExternalSearchConfig(),
        providers_images=ProviderImagesConfig(),
        routing=RoutingConfig(),
        raw={},
    )


def test_find_local_markdown_first_root_hit(tmp_path: Path):
    root_a = tmp_path / "a"
    root_a.mkdir()
    (root_a / "2401.12345.md").write_text("body-a", encoding="utf-8")
    root_b = tmp_path / "b"
    root_b.mkdir()
    (root_b / "2401.12345.md").write_text("body-b", encoding="utf-8")

    cfg = _cfg(root_a, root_b)
    found = find_local_markdown("2401.12345", cfg=cfg)
    assert found == root_a / "2401.12345.md"
    assert read_local_markdown("2401.12345", cfg=cfg) == "body-a"


def test_find_local_markdown_falls_through_missing_root(tmp_path: Path):
    missing = tmp_path / "nope"
    real = tmp_path / "real"
    real.mkdir()
    (real / "2401.12345.md").write_text("ok", encoding="utf-8")

    cfg = _cfg(missing, real)
    assert find_local_markdown("2401.12345", cfg=cfg) == real / "2401.12345.md"


def test_find_local_markdown_returns_none_when_all_miss(tmp_path: Path):
    cfg = _cfg(tmp_path / "a", tmp_path / "b")
    assert find_local_markdown("9999.99999", cfg=cfg) is None
    assert read_local_markdown("9999.99999", cfg=cfg) is None


def test_find_local_markdown_filename_variants(tmp_path: Path):
    root = tmp_path / "store"
    (root / "2401.12345").mkdir(parents=True)
    (root / "2401.12345" / "paper.md").write_text("nested", encoding="utf-8")

    cfg = _cfg(root)
    found = find_local_markdown("2401.12345", cfg=cfg)
    assert found is not None
    assert found.name == "paper.md"


def test_iter_storage_roots_reports_hit_status(tmp_path: Path):
    real = tmp_path / "real"
    real.mkdir()
    (real / "2401.aa.md").write_text("x", encoding="utf-8")
    (real / "2401.bb.md").write_text("x", encoding="utf-8")
    missing = tmp_path / "missing"

    cfg = _cfg(missing, real)
    rows = iter_storage_roots(cfg=cfg)
    assert rows[0] == (missing, False, 0)
    path, exists, count = rows[1]
    assert path == real
    assert exists is True
    assert count == 2


def test_config_yaml_accepts_str_or_list(tmp_path: Path, monkeypatch):
    """yaml `arxiv_mcp_storage_path` can be either a string or a list."""
    import yaml

    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic"))
    from paic.config import load_config, reset_config_cache

    # str form
    home = tmp_path / ".paic"
    home.mkdir()
    (home / "config.yaml").write_text(
        yaml.safe_dump({"arxiv_mcp_storage_path": str(tmp_path / "single")}),
        encoding="utf-8",
    )
    reset_config_cache()
    cfg = load_config()
    assert cfg.arxiv_mcp_storage_paths[0] == tmp_path / "single"
    # defaults still appended
    assert len(cfg.arxiv_mcp_storage_paths) >= 2

    # list form
    (home / "config.yaml").write_text(
        yaml.safe_dump(
            {"arxiv_mcp_storage_paths": [str(tmp_path / "x"), str(tmp_path / "y")]}
        ),
        encoding="utf-8",
    )
    reset_config_cache()
    cfg2 = load_config()
    assert cfg2.arxiv_mcp_storage_paths[:2] == (tmp_path / "x", tmp_path / "y")
