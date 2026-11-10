"""Contract-valid form manifests for tests (not collected).

Every manifest here is fictional TEST DATA. Nothing describes a real government
form, and no field is an official field definition.
"""

from __future__ import annotations

import hashlib
import io
from typing import Any

from pypdf import PdfReader


def contract_manifest(
    pdf: bytes,
    *,
    semver: str = "1.0.0",
    slug: str = "ror-7-12",
    jurisdiction: str = "CENTRAL_INDIA",
    category: str = "REVENUE",
    official_form_code: str = "RTC-7/12",
) -> dict[str, Any]:
    """Smallest manifest that satisfies form-manifest v1 for ``pdf``."""
    return {
        "schema_version": 1,
        "kind": "govforms.form-manifest",
        "identity": {
            "slug": slug,
            "jurisdiction": jurisdiction,
            "category": category,
            "official_form_code": official_form_code,
            "semver": semver,
            "page_count": len(PdfReader(io.BytesIO(pdf)).pages),
            "blank_pdf_sha256": hashlib.sha256(pdf).hexdigest(),
        },
        "sections": [
            {
                "id": "applicant",
                "title": "Applicant (test fixture)",
                "fields": [
                    {
                        "id": "applicant_name",
                        "type": "text",
                        "label": "Applicant name",
                        "required": True,
                        "pdf": [
                            {
                                "type": "text_at",
                                "page": 0,
                                "x": 72,
                                "y": 700,
                                "width": 300,
                                "height": 18,
                                "font": "Helvetica",
                                "size": 11,
                                "align": "left",
                                "multiline": False,
                            }
                        ],
                    }
                ],
            }
        ],
    }
