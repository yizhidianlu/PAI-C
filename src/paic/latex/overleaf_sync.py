"""Bidirectional sync between PAI-C drafts/ and an Overleaf-linked Dropbox folder.

Mechanism: Overleaf's account-level Dropbox integration creates
``~/Dropbox/Apps/Overleaf/`` and maps each subdirectory under it to one
Overleaf project (no API key, no Premium git integration required). PAI-C
mirrors ``<project>/.paic/drafts/`` into the matching subdirectory and
pulls back any Overleaf-side edits via three-way merge against a baseline
manifest at ``<project>/.paic/state/overleaf_sync.yaml``.

Each file is classified by comparing local-vs-baseline AND
remote-vs-baseline. Conflicts (both sides modified differently) are
resolved per ``conflict_strategy`` from ``OverleafConfig``; the default
``keep_both`` is non-destructive — local stays put, the remote version
is pulled into the local tree as ``<name>.overleaf-conflict.<UTC>.<ext>``
for the user to merge by hand.

Deletes are gated: a unilateral delete on one side ends up in
``deletions_pending`` (not auto-propagated) unless the caller passes
``confirm_deletions=True``.
"""

from __future__ import annotations

import fnmatch
import hashlib
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from paic.config import OverleafConfig
from paic.workspace.paths import ProjectPaths
from paic.workspace.store import load_yaml, save_yaml

ConflictStrategy = Literal["keep_both", "local_wins", "remote_wins", "newer_wins"]
Direction = Literal["auto", "push_only", "pull_only"]

# Sentinel used inside _resolve_conflict to signal "drop from baseline".
_REMOVE_FROM_BASELINE: dict[str, Any] = {}


# ---------------------------------------------------------------- scan helpers

def _sha256(path: Path) -> str:
    """Stream sha256 for files of any size."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _matches_ignore(name: str, patterns: tuple[str, ...] | list[str]) -> bool:
    return any(fnmatch.fnmatch(name, p) for p in patterns)


def _scan_dir(
    root: Path, ignore_patterns: tuple[str, ...] | list[str]
) -> dict[str, dict[str, Any]]:
    """Walk root, return ``{rel_posix_path: {sha256, mtime, size}}``.

    Files matching any ignore pattern (by basename) are skipped. Returns ``{}``
    if root doesn't exist (first-sync remote case).
    """
    out: dict[str, dict[str, Any]] = {}
    if not root.is_dir():
        return out
    for p in root.rglob("*"):
        if not p.is_file() or _matches_ignore(p.name, ignore_patterns):
            continue
        rel = p.relative_to(root).as_posix()
        st = p.stat()
        out[rel] = {
            "sha256": _sha256(p),
            "mtime": int(st.st_mtime),
            "size": st.st_size,
        }
    return out


def _count_ignored(
    drafts: Path, target: Path, patterns: tuple[str, ...] | list[str]
) -> int:
    n = 0
    for root in (drafts, target):
        if not root.is_dir():
            continue
        for p in root.rglob("*"):
            if p.is_file() and _matches_ignore(p.name, patterns):
                n += 1
    return n


# ---------------------------------------------------------- target / baseline

def _resolve_target_dir(
    project_paths: ProjectPaths,
    overleaf_cfg: OverleafConfig,
    target_dir_arg: str | None,
) -> Path:
    """Compute the Overleaf project subdirectory.

    Priority: explicit ``target_dir_arg`` > config ``target_root`` joined with
    ``project_subdir`` (or the project root's basename when subdir is unset).
    """
    if target_dir_arg:
        return Path(target_dir_arg).expanduser()
    subdir = overleaf_cfg.project_subdir or project_paths.root.name
    return overleaf_cfg.target_root / subdir


def _load_baseline(state_path: Path) -> dict[str, dict[str, Any]]:
    """Load ``files`` block from baseline manifest. Empty dict on first sync."""
    if not state_path.exists():
        return {}
    data = load_yaml(state_path) or {}
    files = data.get("files") if isinstance(data, dict) else None
    if not isinstance(files, dict):
        return {}
    return {k: v for k, v in files.items() if isinstance(v, dict)}


def _save_baseline(
    state_path: Path, target_dir: Path, files: dict[str, dict[str, Any]]
) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    save_yaml(state_path, {
        "last_sync_at": datetime.now(UTC).isoformat(),
        "target_dir": str(target_dir),
        "files": files,
    })


# ----------------------------------------------------------- classification

def _classify(
    local: dict[str, dict[str, Any]],
    remote: dict[str, dict[str, Any]],
    baseline: dict[str, dict[str, Any]],
) -> dict[str, str]:
    """Return ``{rel_path: state}`` covering every path seen on either side or
    in the baseline.

    States:
      unchanged
      local_new                       (in local, not remote, not baseline)
      remote_new                      (in remote, not local, not baseline)
      both_new_same / both_new_diff   (in local + remote, not baseline)
      local_modified / remote_modified
      both_modified_same / both_modified_diff
      local_deleted / remote_deleted  (deleted on one side, untouched on the other)
      both_deleted
      remote_deleted_local_modified   (conflict: deleted remotely, edited locally)
      local_deleted_remote_modified   (conflict: deleted locally, edited remotely)
    """
    paths = set(local) | set(remote) | set(baseline)
    out: dict[str, str] = {}
    for p in paths:
        loc = local.get(p)
        rem = remote.get(p)
        base = baseline.get(p)
        l_hash = loc["sha256"] if loc else None
        r_hash = rem["sha256"] if rem else None
        b_hash = base["sha256"] if base else None
        in_baseline = base is not None

        if loc and rem and not in_baseline:
            out[p] = "both_new_same" if l_hash == r_hash else "both_new_diff"
        elif loc and not rem and not in_baseline:
            out[p] = "local_new"
        elif rem and not loc and not in_baseline:
            out[p] = "remote_new"
        elif loc and rem and in_baseline:
            l_changed = l_hash != b_hash
            r_changed = r_hash != b_hash
            if not l_changed and not r_changed:
                out[p] = "unchanged"
            elif l_changed and not r_changed:
                out[p] = "local_modified"
            elif r_changed and not l_changed:
                out[p] = "remote_modified"
            else:
                out[p] = "both_modified_same" if l_hash == r_hash else "both_modified_diff"
        elif loc and not rem and in_baseline:
            out[p] = "remote_deleted_local_modified" if l_hash != b_hash else "remote_deleted"
        elif rem and not loc and in_baseline:
            out[p] = "local_deleted_remote_modified" if r_hash != b_hash else "local_deleted"
        elif not loc and not rem and in_baseline:
            out[p] = "both_deleted"
    return out


def _conflict_filename(rel_path: str, ts: str) -> str:
    """Return ``<stem>.overleaf-conflict.<ts><suffixes>``.

    Preserves all suffixes (``main.tex.j2`` → ``main.overleaf-conflict.<ts>.tex.j2``).
    """
    p = Path(rel_path)
    stem = p.name
    # Strip suffixes from the basename to get the stem without dots
    while True:
        suf = Path(stem).suffix
        if not suf:
            break
        stem = stem[: -len(suf)]
    suffixes = "".join(p.suffixes)
    parent = p.parent.as_posix()
    fname = f"{stem}.overleaf-conflict.{ts}{suffixes}"
    return fname if parent in ("", ".") else f"{parent}/{fname}"


# ---------------------------------------------------------------- main entry


def mirror_drafts_to_overleaf(
    project_paths: ProjectPaths,
    overleaf_cfg: OverleafConfig,
    *,
    target_dir: str | None = None,
    direction: Direction = "auto",
    dry_run: bool = False,
    conflict_strategy: ConflictStrategy | None = None,
    confirm_deletions: bool = False,
) -> dict[str, Any]:
    """Three-way bidirectional sync between drafts/ and the Overleaf folder.

    Returns a report dict with ``pushed`` / ``pulled`` / ``conflicts`` /
    ``deletions_pending`` / ``deletions_propagated`` / ``ignored`` / ``dry_run``,
    or ``{"error": "<reason>", ...}`` on hard failures (config disabled, drafts
    missing, target_root unreachable).
    """
    if not overleaf_cfg.enabled:
        return {
            "error": "overleaf_disabled",
            "hint": "Set overleaf.enabled: true in ~/.paic/config.yaml + restart Claude Code.",
        }

    drafts = project_paths.drafts_dir
    if not drafts.is_dir():
        return {
            "error": "drafts_dir_not_found",
            "drafts_dir": str(drafts),
            "hint": "Run /paic-draft fill first to scaffold the LaTeX skeleton.",
        }

    target = _resolve_target_dir(project_paths, overleaf_cfg, target_dir)
    if not target.parent.exists():
        return {
            "error": "overleaf_target_root_missing",
            "looked_for": str(target.parent),
            "hint": (
                "Open Overleaf → Account Settings → Linked Accounts → connect "
                "Dropbox; ~/Dropbox/Apps/Overleaf/ is auto-created on first connect."
            ),
        }

    strategy: ConflictStrategy = conflict_strategy or overleaf_cfg.conflict_strategy
    state_path = project_paths.state_dir / "overleaf_sync.yaml"

    local = _scan_dir(drafts, overleaf_cfg.ignore_patterns)
    remote = _scan_dir(target, overleaf_cfg.ignore_patterns)
    baseline = _load_baseline(state_path)
    states = _classify(local, remote, baseline)

    pushed: list[str] = []
    pulled: list[str] = []
    conflicts: list[dict[str, Any]] = []
    deletions_pending: list[dict[str, Any]] = []
    deletions_propagated: list[dict[str, Any]] = []
    new_baseline: dict[str, dict[str, Any]] = {}
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")

    push_blocked = direction == "pull_only"
    pull_blocked = direction == "push_only"

    def copy_to(src: Path, dst: Path) -> None:
        if dry_run:
            return
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    for rel, state in states.items():
        if state == "unchanged":
            new_baseline[rel] = baseline[rel]
            continue

        if state in ("local_modified", "local_new"):
            if push_blocked:
                # Block push: keep prior baseline so next sync revisits.
                if rel in baseline:
                    new_baseline[rel] = baseline[rel]
                continue
            copy_to(drafts / rel, target / rel)
            pushed.append(rel)
            new_baseline[rel] = dict(local[rel])

        elif state in ("remote_modified", "remote_new"):
            if pull_blocked:
                if rel in baseline:
                    new_baseline[rel] = baseline[rel]
                continue
            copy_to(target / rel, drafts / rel)
            pulled.append(rel)
            new_baseline[rel] = dict(remote[rel])

        elif state in ("both_new_same", "both_modified_same"):
            # Converged independently — promote to baseline.
            new_baseline[rel] = dict(local[rel])

        elif state in (
            "both_new_diff",
            "both_modified_diff",
            "remote_deleted_local_modified",
            "local_deleted_remote_modified",
        ):
            resolved = _resolve_conflict(
                strategy=strategy,
                rel=rel,
                ts=ts,
                state=state,
                local=local,
                remote=remote,
                drafts=drafts,
                target=target,
                push_blocked=push_blocked,
                pull_blocked=pull_blocked,
                copy_to=copy_to,
                dry_run=dry_run,
            )
            conflicts.append(resolved)
            rec = resolved.get("new_baseline_record")
            if rec:
                new_baseline[rel] = rec
            # Empty/missing rec means "drop from baseline" (file gone after resolve).

        elif state in ("local_deleted", "remote_deleted"):
            deleted_on = "local" if state == "local_deleted" else "remote"
            propagate = confirm_deletions and overleaf_cfg.prompt_on_delete
            if not propagate:
                deletions_pending.append({"path": rel, "deleted_on": deleted_on})
                # Keep baseline so the user can revisit.
                new_baseline[rel] = baseline[rel]
                continue
            # Propagate the deletion to the still-present side.
            if deleted_on == "local" and not pull_blocked:
                if not dry_run and (target / rel).exists():
                    (target / rel).unlink()
                deletions_propagated.append(
                    {"path": rel, "deleted_on": "local", "removed_at": "remote"}
                )
            elif deleted_on == "remote" and not push_blocked:
                if not dry_run and (drafts / rel).exists():
                    (drafts / rel).unlink()
                deletions_propagated.append(
                    {"path": rel, "deleted_on": "remote", "removed_at": "local"}
                )
            else:
                # Direction blocks the propagation — fall back to pending.
                deletions_pending.append({"path": rel, "deleted_on": deleted_on})
                new_baseline[rel] = baseline[rel]
            # Otherwise: file is gone on both sides → drop from baseline.

        elif state == "both_deleted":
            # Drop from baseline — file gone everywhere.
            pass

    if not dry_run:
        _save_baseline(state_path, target, new_baseline)

    return {
        "target_dir": str(target),
        "pushed": pushed,
        "pulled": pulled,
        "conflicts": conflicts,
        "deletions_pending": deletions_pending,
        "deletions_propagated": deletions_propagated,
        "ignored": _count_ignored(drafts, target, overleaf_cfg.ignore_patterns),
        "dry_run": dry_run,
        "baseline_at": datetime.now(UTC).isoformat(),
        "conflict_strategy": strategy,
        "direction": direction,
    }


# -------------------------------------------------------- conflict resolution


def _resolve_conflict(
    *,
    strategy: ConflictStrategy,
    rel: str,
    ts: str,
    state: str,
    local: dict[str, dict[str, Any]],
    remote: dict[str, dict[str, Any]],
    drafts: Path,
    target: Path,
    push_blocked: bool,
    pull_blocked: bool,
    copy_to,
    dry_run: bool,
) -> dict[str, Any]:
    """Apply ``strategy`` to a single conflicting path; return a record
    describing the outcome plus the new baseline record (or empty dict to
    signal "drop from baseline")."""
    record: dict[str, Any] = {"path": rel, "state": state, "strategy": strategy}

    has_local = rel in local
    has_remote = rel in remote

    if strategy == "keep_both":
        # Symmetric: pull remote into local under conflict name (if remote
        # exists), and push local back over remote (if local exists). If
        # one side was a deletion, "keep_both" means revive from the
        # surviving side.
        if has_remote and has_local:
            conflict_rel = _conflict_filename(rel, ts)
            if not pull_blocked:
                copy_to(target / rel, drafts / conflict_rel)
                record["remote_kept_as"] = conflict_rel
            if not push_blocked:
                copy_to(drafts / rel, target / rel)
                record["pushed_local"] = True
            record["new_baseline_record"] = dict(local[rel])
        elif has_local and not has_remote:  # remote_deleted_local_modified
            if not push_blocked:
                copy_to(drafts / rel, target / rel)
                record["pushed_local"] = True
            record["new_baseline_record"] = dict(local[rel])
        elif has_remote and not has_local:  # local_deleted_remote_modified
            if not pull_blocked:
                copy_to(target / rel, drafts / rel)
                record["pulled_remote"] = True
            record["new_baseline_record"] = dict(remote[rel])
        else:
            record["new_baseline_record"] = {}

    elif strategy == "local_wins":
        if has_local:
            if not push_blocked:
                copy_to(drafts / rel, target / rel)
                record["pushed_local"] = True
            record["new_baseline_record"] = dict(local[rel])
        else:  # local_deleted_remote_modified — local "wins" the deletion
            if not pull_blocked and not dry_run and (target / rel).exists():
                (target / rel).unlink()
                record["remote_deleted"] = True
            record["new_baseline_record"] = {}

    elif strategy == "remote_wins":
        if has_remote:
            if not pull_blocked:
                copy_to(target / rel, drafts / rel)
                record["pulled_remote"] = True
            record["new_baseline_record"] = dict(remote[rel])
        else:  # remote_deleted_local_modified — remote "wins" the deletion
            if not push_blocked and not dry_run and (drafts / rel).exists():
                (drafts / rel).unlink()
                record["local_deleted"] = True
            record["new_baseline_record"] = {}

    elif strategy == "newer_wins":
        l_mtime = local.get(rel, {}).get("mtime", 0)
        r_mtime = remote.get(rel, {}).get("mtime", 0)
        # Treat a missing side as mtime=0 — the surviving side wins.
        if l_mtime >= r_mtime and has_local:
            if not push_blocked:
                copy_to(drafts / rel, target / rel)
            record["winner"] = "local"
            record["new_baseline_record"] = dict(local[rel])
        elif has_remote:
            if not pull_blocked:
                copy_to(target / rel, drafts / rel)
            record["winner"] = "remote"
            record["new_baseline_record"] = dict(remote[rel])
        else:
            record["new_baseline_record"] = {}

    return record
