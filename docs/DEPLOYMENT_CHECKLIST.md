# Deployment Checklist

The prototype is **not production-ready**. Every unchecked item is a blocker.

## External decisions (see DECISION_REGISTER.md)

- [ ] DEC-1 production publisher key approved; public key + `kid` pinned in the build
- [ ] DEC-2 compromise-recovery policy approved and rehearsed once
- [ ] DEC-3 storage/CDN provider selected; production adapter implemented and tested
- [ ] DEC-4 real frontend origins known and set in `CORS_ALLOWED_ORIGINS`
- [ ] DEC-5 secrets provisioned in a secret manager, identical across instances
- [ ] DEC-6 SMS provider integrated (`OTP_DELIVERY_PROVIDER=sms`)
- [ ] DEC-7 refresh-token storage decision
- [ ] COM-01 quota/pricing approved; LEG-1 legal review; M7 eSign remains off

## Backend configuration (startup refuses to run if violated in staging/production)

- [ ] `ENVIRONMENT=production`, `DEBUG=false`
- [ ] `DATABASE_URL` → managed PostgreSQL 16; `alembic upgrade head` (→ 0003)
- [ ] `BLIND_INDEX_HMAC_KEY`, `OTP_HMAC_KEY`, `IP_SUBNET_HASH_KEY` — real, distinct
- [ ] `ACCESS_JWT_ED25519_SEED_B64`, `LEASE_SIGNING_SEED_B64`,
      `TEMPLATE_SIGNING_ED25519_SEED_B64` — 32-byte seeds, mutually distinct, stable
- [ ] `TEMPLATE_SIGNING_KEY_ID` matches the kid pinned in the frontend build
- [ ] `TEMPLATE_SIGNING_PREV_*` only during a planned rotation
- [ ] `CORS_ALLOWED_ORIGINS` — exact https origins, no wildcard
- [ ] `TEMPLATE_STORAGE_PROVIDER` = production adapter; `TEMPLATE_CDN_BASE_URL` = CDN
- [ ] `RAZORPAY_*` live credentials, `RAZORPAY_ENVIRONMENT=live`, webhook secret set
- [ ] Rate limiter backed by a shared store if more than one instance runs
- [ ] `scripts/seed_dev.py`, `scripts/dev_cdn.py`, `scripts/make_dev_env.py` not deployed
      (they also refuse to run in shared environments)

## Frontend build

- [ ] `VITE_APP_ENV=production`, `VITE_API_BASE_URL=https://…`
- [ ] `VITE_TEMPLATE_PUBLISHER_KEYS` = approved key(s) only
- [ ] `VITE_TEMPLATE_CDN_HOST` = approved CDN hostname
- [ ] `VITE_DEV_TEMPLATE_CDN_ORIGIN` unset (the build refuses it anyway)
- [ ] CSP reviewed: `connect-src` limited to the API and CDN; `worker-src` allows the
      bundled pdf.js worker; no third-party script origins
- [ ] Lovable error reporting reviewed for the production host (errors carry no answer
      values by construction, but the channel should be confirmed or disabled)

## Verification before go-live

- [ ] Full backend and frontend suites green in CI on PostgreSQL 16
- [ ] Browser walkthrough repeated against staging with the real CDN
- [ ] Network capture confirms only auth, catalogue, bundle, status, entitlement and
      payment calls leave the browser
- [ ] At least one real government template taken through maker-checker with a
      reviewed manifest (none exists today — all current templates are test fixtures)
- [ ] Penetration test / independent security review
