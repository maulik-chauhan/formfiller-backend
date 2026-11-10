# Contract fixtures — TEST DATA ONLY

Everything in this directory is fictional. No PDF here is a government form and no
manifest is an official field definition. They exist so the backend and frontend can
prove they interpret the form-manifest contract identically.

- `pdfs/` — two generated blank PDFs (`frontend/.../scripts/make-fixture-pdfs.mjs`).
- `manifests/valid/` — eight manifests both validators must accept.
- `manifests/invalid/` — `{description, expect_code, manifest}`; both validators must
  report `expect_code`.
- `vectors/canonical-json.json`, `manifest-digests.json` — canonical JSON and SHA-256.
- `vectors/signature.json` — v1/v2 bundle digests signed with the published TEST key.
- `vectors/fill-cases.json` — answers → validation errors and printed values.
- `index.json` — SHA-256 of every file above.

Regenerate with `python -m scripts.build_contract_fixtures` (from `backend/`), then
`python -m scripts.sync_contracts ../frontend/screenshot-to-sight-3914`.
