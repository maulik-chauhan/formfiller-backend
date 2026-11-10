"""Copy the canonical contract (docs + schema + fixtures) into the frontend repo.

    python -m scripts.sync_contracts <path-to-frontend-repo>
    python -m scripts.sync_contracts <path-to-frontend-repo> --check

The two repositories are separate; the frontend keeps a byte-identical copy in
``contracts/`` so its tests run standalone. ``fixtures/index.json`` pins every
file's SHA-256, and both test suites verify their copy against it.
"""

from __future__ import annotations

import filecmp
import pathlib
import shutil
import sys

SOURCE = pathlib.Path(__file__).resolve().parents[2] / "docs" / "contracts"


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 1:
        raise SystemExit("usage: python -m scripts.sync_contracts <frontend-repo> [--check]")
    target = pathlib.Path(args[0]).resolve() / "contracts"
    files = [p for p in SOURCE.rglob("*") if p.is_file() and p.name != ".DS_Store"]
    if "--check" in sys.argv:
        stale = [
            str(p.relative_to(SOURCE))
            for p in files
            if not (target / p.relative_to(SOURCE)).exists()
            or not filecmp.cmp(p, target / p.relative_to(SOURCE), shallow=False)
        ]
        extra = (
            [
                str(p.relative_to(target))
                for p in target.rglob("*")
                if p.is_file() and not (SOURCE / p.relative_to(target)).exists()
            ]
            if target.exists()
            else []
        )
        if stale or extra:
            raise SystemExit(f"frontend contracts are out of date: stale={stale} extra={extra}")
        print(f"frontend contracts match ({len(files)} files)")
        return
    if target.exists():
        shutil.rmtree(target)
    for path in files:
        destination = target / path.relative_to(SOURCE)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, destination)
    print(f"copied {len(files)} files to {target}")


if __name__ == "__main__":
    main()
