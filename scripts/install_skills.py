"""Install PAI-C skills into ``~/.claude/skills/``.

Each ``skills/paic-*/`` directory in this repo becomes ``~/.claude/skills/paic-*/``.
By default we **copy** the SKILL.md (safe across uv venvs and easier to edit
in place); pass ``--symlink`` to use junction/symlink instead so updates in
this repo propagate without re-running.

Pass ``--remove`` to delete previously installed skills.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS_SRC = REPO_ROOT / "skills"
SKILLS_DST = Path.home() / ".claude" / "skills"


def iter_skill_dirs() -> list[Path]:
    if not SKILLS_SRC.is_dir():
        return []
    return sorted(p for p in SKILLS_SRC.iterdir() if p.is_dir() and p.name.startswith("paic-"))


def install(use_symlink: bool) -> None:
    SKILLS_DST.mkdir(parents=True, exist_ok=True)
    skills = iter_skill_dirs()
    if not skills:
        print(f"No skills found under {SKILLS_SRC}")
        return

    for src in skills:
        dst = SKILLS_DST / src.name
        if dst.exists() or dst.is_symlink():
            if dst.is_symlink() or dst.is_dir():
                if dst.is_symlink():
                    dst.unlink()
                else:
                    shutil.rmtree(dst)
        if use_symlink:
            try:
                dst.symlink_to(src, target_is_directory=True)
                kind = "symlinked"
            except OSError as exc:
                print(f"Symlink failed for {src.name} ({exc}); falling back to copy.", file=sys.stderr)
                shutil.copytree(src, dst)
                kind = "copied"
        else:
            shutil.copytree(src, dst)
            kind = "copied"
        print(f"  {kind:>10}  {src.name}")
    print(f"\nInstalled {len(skills)} skill(s) into {SKILLS_DST}")


def remove() -> None:
    skills = iter_skill_dirs()
    removed = 0
    for src in skills:
        dst = SKILLS_DST / src.name
        if dst.is_symlink():
            dst.unlink()
            removed += 1
            print(f"  removed (link)  {src.name}")
        elif dst.is_dir():
            shutil.rmtree(dst)
            removed += 1
            print(f"  removed (dir)   {src.name}")
    print(f"\nRemoved {removed} skill(s) from {SKILLS_DST}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symlink", action="store_true", help="Use symlinks instead of copies")
    parser.add_argument("--remove", action="store_true", help="Uninstall PAI-C skills")
    args = parser.parse_args()
    if args.remove:
        remove()
    else:
        install(use_symlink=args.symlink)


if __name__ == "__main__":
    main()
