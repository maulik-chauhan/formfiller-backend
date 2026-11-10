# Publisher Key Compromise — Draft Runbook (DEC-2)

**Status: draft, not approved, never rehearsed.** It describes what the current code
supports. It is not evidence of incident readiness.

## Trigger

Suspected or confirmed disclosure of `TEMPLATE_SIGNING_ED25519_SEED_B64` (or the
previous seed), or an unexplained published template.

## 1. Contain (minutes)

1. Freeze publication: suspend the approver accounts (`account_status`), which blocks
   every `APPROVED → PUBLISHED` transition.
2. Generate a new seed in the secret manager with a **new `kid`**. Deploy it as
   `TEMPLATE_SIGNING_ED25519_SEED_B64` / `TEMPLATE_SIGNING_KEY_ID`.
3. Do **not** configure the compromised key as `TEMPLATE_SIGNING_PREV_*`. From this
   deploy the backend reports `signature_verified: false` for everything it signed and
   `/templates/publisher-keys` no longer lists it.

## 2. Revoke on clients (the step that actually protects users)

4. Ship a frontend build whose `VITE_TEMPLATE_PUBLISHER_KEYS` contains the new `kid`
   and **omits the compromised one**. Clients trust only build-pinned keys, so the
   server cannot revoke for them — a release is required.
5. Effect on cached templates: every cached template is re-verified against the pinned
   keys each time it is loaded. A template signed by the removed key fails and is
   purged from the device; the user must prepare it again. Drafts and answers are kept
   and stay tied to their template version.
6. Clients that have not updated keep trusting the old key. Decide (open question) how
   to force the update: minimum-version gate, service-worker update, or cache headers.

## 3. Recover

7. Audit `template_versions` rows with the compromised `signing_key_id` against
   `template_approval_logs` and `audit_logs`. Anything without a complete, three-person
   maker-checker trail is treated as forged: transition it to `DEPRECATED` with a
   `deprecation_reason`.
8. Legitimate templates: published rows are immutable and are not re-signed. Deprecate
   each one and publish the same PDF/manifest as a new `semver` through the normal
   review chain so it is signed by the new key. Set `replaced_by_template_slug` where a
   template is superseded by a differently named one.
9. Re-enable approver accounts.

## 4. Open questions for the approver

- Who may declare a compromise, and within what time must step 4 ship?
- Is there an offline or secondary pinned key so a client release is not the only
  recovery path?
- How are users told that a form they already printed came from a withdrawn template?
- Forced-update mechanism for old clients (step 6).

## What has been tested

Automated only: retiring a key makes its signatures fail on the backend; a client with
no pin for a `kid` rejects the bundle and purges the cached copy; rotation with a
previous key keeps old signatures valid. No end-to-end drill has been run.
