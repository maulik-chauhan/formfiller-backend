"""Access to the shared form-manifest contract fixtures (not collected)."""

from __future__ import annotations

import json
import pathlib
from typing import Any

CONTRACTS = pathlib.Path(__file__).resolve().parents[2] / "docs" / "contracts"
FIXTURES = CONTRACTS / "fixtures"


def load(relative: str) -> Any:
    return json.loads((FIXTURES / relative).read_text(encoding="utf-8"))


def valid_manifests() -> dict[str, dict[str, Any]]:
    return {
        p.stem: json.loads(p.read_text()) for p in sorted(FIXTURES.glob("manifests/valid/*.json"))
    }


def invalid_cases() -> dict[str, dict[str, Any]]:
    return {
        p.stem: json.loads(p.read_text()) for p in sorted(FIXTURES.glob("manifests/invalid/*.json"))
    }


def pdf_bytes(name: str) -> bytes:
    return (FIXTURES / "pdfs" / name).read_bytes()
