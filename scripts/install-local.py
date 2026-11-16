"""Copy this checkout's tracked runtime into an existing local workspace.

Preserves data, credentials, local policy and historical evidence. A single
backup contains overwritten files, not a new per-request verification chain.
"""

import argparse
from datetime import datetime, timezone
from pathlib import Path
import shutil
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[1]
    target = args.destination.resolve(strict=True)
    if target == source or not (target / "data/project_evidence.sqlite3").is_file():
        raise SystemExit("Choose the existing local workspace with its original database")
    names = subprocess.check_output(
        ["git", "ls-files", "-z"], cwd=source,
    ).decode().split("\0")
    roots = {"src", "static", "prompts", "schemas", "scripts", "evaluation"}
    files = [Path(name) for name in names if name and (
        Path(name).parts[0] in roots or name in {
            "run.py", "pyproject.toml", "requirements.lock.txt", "README.md", "THIRD-PARTY.md",
            "docs/code-guide.md", "docs/model-configuration.md", "docs/known-limitations.md",
            "docs/evaluation-v05.md", "docs/release-0.5.md", "docs/verification.md",
        }
    )]
    backup = target / "data" / "upgrade-backups" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup.mkdir(parents=True, exist_ok=False)
    for relative in files:
        original = (source / relative).resolve(strict=True)
        destination = target / relative
        if source not in original.parents or target not in destination.resolve().parents:
            raise SystemExit("Copy path escaped its workspace")
        if destination.exists():
            if destination.is_symlink():
                raise SystemExit("Refuse to overwrite a linked destination")
            saved = backup / relative
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(destination, saved)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(original, destination)
    print(f"Copied {len(files)} runtime/document files; original database and history preserved")
    print(f"Backup: {backup}")


if __name__ == "__main__":
    main()
