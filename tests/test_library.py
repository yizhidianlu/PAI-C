"""Library tools tests — Phase 3."""

from typing import Any

from paic.mcp_server.tools.library import (
    dedupe_tool,
    library_add_tool,
    s2_search_tool,
)
from paic.mcp_server.tools.workspace import workspace_init


class _FakeResponse:
    def __init__(self, payload: dict[str, Any], status: int = 200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls: list[dict] = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append({"url": url, "params": params})
        return _FakeResponse(self.payload)

    def close(self):
        pass


def test_s2_search_maps_payload_to_paper_refs(monkeypatch, tmp_path):
    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic"))
    from paic.config import reset_config_cache
    from paic.sources import semanticscholar

    reset_config_cache()

    payload = {
        "data": [
            {
                "paperId": "abcd",
                "externalIds": {"ArXiv": "2401.12345", "DOI": "10.1234/x"},
                "title": "Sample Paper",
                "authors": [{"name": "A"}, {"name": "B"}],
                "year": 2025,
                "venue": "NeurIPS",
                "abstract": "abstract",
            }
        ],
        "total": 1,
    }
    client = _FakeClient(payload)
    res = semanticscholar.search_papers(
        "diffusion", limit=5, client=client, _skip_rate_limit=True
    )
    assert res.total == 1
    assert res.papers[0].arxiv_id == "2401.12345"
    assert res.papers[0].source == "s2"


def test_s2_search_uses_cache(monkeypatch, tmp_path):
    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic"))
    from paic.config import reset_config_cache
    from paic.sources import semanticscholar

    reset_config_cache()

    payload = {
        "data": [
            {
                "paperId": "x",
                "externalIds": {},
                "title": "T",
                "authors": [],
            }
        ],
        "total": 1,
    }

    client1 = _FakeClient(payload)
    semanticscholar.search_papers("q", client=client1, _skip_rate_limit=True)
    assert len(client1.calls) == 1

    # Second call same query should hit cache and not call client at all.
    client2 = _FakeClient(payload)
    res = semanticscholar.search_papers("q", client=client2, _skip_rate_limit=True)
    assert client2.calls == []
    assert res.from_cache is True


def test_dedupe_tool_round_trips_dicts():
    out = dedupe_tool(
        [
            {"arxiv_id": "2401.0001", "title": "A"},
            {"arxiv_id": "2401.0001", "title": "A (v2)"},
            {"arxiv_id": "2401.0002", "title": "B"},
        ]
    )
    assert len(out["unique"]) == 2
    assert out["duplicate_groups"] == [[0, 1]]


def test_library_add_dedupes_against_existing(tmp_path, monkeypatch):
    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic"))
    project = tmp_path / "p"
    workspace_init(project)

    first = library_add_tool(
        str(project),
        [
            {"arxiv_id": "2401.0001", "title": "A"},
            {"arxiv_id": "2401.0002", "title": "B"},
        ],
        tags=["topic-x"],
    )
    assert len(first["added"]) == 2
    assert first["library_count"] == 2

    second = library_add_tool(
        str(project),
        [
            {"arxiv_id": "2401.0001", "title": "A reposted"},  # dup
            {"arxiv_id": "2401.0003", "title": "C"},
        ],
    )
    assert [p["arxiv_id"] for p in second["added"]] == ["2401.0003"]
    assert [p["arxiv_id"] for p in second["skipped_duplicates"]] == ["2401.0001"]
    assert second["library_count"] == 3


def test_library_add_uninitialized_project(tmp_path):
    res = library_add_tool(str(tmp_path / "missing"), [{"arxiv_id": "1", "title": "x"}])
    assert res["error"] == "project_not_initialized"
