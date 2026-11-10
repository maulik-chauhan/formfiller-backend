"""Write the backend's OpenAPI document to the shared contracts directory.

    python -m scripts.export_openapi            # (re)write docs/contracts/openapi.json
    python -m scripts.export_openapi --check    # fail if it is out of date

The frontend's copy (``contracts/openapi.json``) is checked by its own test suite
against the response schemas it validates at runtime, so an API change that is not
reflected on both sides fails a test on at least one of them.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys

TARGET = pathlib.Path(__file__).resolve().parents[2] / "docs" / "contracts" / "openapi.json"


def render() -> str:
    # Deterministic, environment-independent output.
    os.environ.setdefault("ENVIRONMENT", "test")
    from app.main import create_app

    return json.dumps(create_app().openapi(), indent=2, sort_keys=True) + "\n"


def main() -> None:
    text = render()
    if "--check" in sys.argv:
        if not TARGET.exists() or TARGET.read_text() != text:
            raise SystemExit("docs/contracts/openapi.json is out of date")
        print("openapi.json is up to date")
        return
    TARGET.write_text(text)
    print(f"wrote {TARGET}")


if __name__ == "__main__":
    main()
