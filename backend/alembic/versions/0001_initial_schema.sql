-- =============================================================================
-- Indian Government Forms Automation Platform — Initial schema (19 tables)
-- Canonical DDL per architecture spec v2.1.0, Section 5.3.
-- =============================================================================

-- =============================================================================
-- MODULE 1: AUTHENTICATION & IDENTITY (Tables 1 - 4)
-- =============================================================================

CREATE TABLE users (
    id UUID PRIMARY KEY,
    mobile_blind_index CHAR(64) UNIQUE,
    email_blind_index CHAR(64) UNIQUE,
    blind_index_key_version SMALLINT NOT NULL DEFAULT 1,
    role VARCHAR(24) NOT NULL DEFAULT 'CITIZEN',
    account_status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',
    is_mobile_verified BOOLEAN NOT NULL DEFAULT FALSE,
    is_email_verified BOOLEAN NOT NULL DEFAULT FALSE,
    mobile_verified_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_login_at TIMESTAMPTZ,
    deleted_at TIMESTAMPTZ,
    CONSTRAINT chk_users_role CHECK (
        role IN ('CITIZEN', 'TEMPLATE_CURATOR', 'TEMPLATE_VERIFIER', 'TEMPLATE_APPROVER', 'SUPER_ADMIN')
    ),
    CONSTRAINT chk_users_account_status CHECK (
        account_status IN ('ACTIVE', 'SUSPENDED', 'DELETED')
    ),
    CONSTRAINT chk_users_active_requires_mobile_index CHECK (
        (account_status = 'DELETED' AND mobile_blind_index IS NULL AND deleted_at IS NOT NULL)
        OR (account_status IN ('ACTIVE', 'SUSPENDED') AND mobile_blind_index IS NOT NULL AND mobile_blind_index ~ '^[a-f0-9]{64}$')
    ),
    CONSTRAINT chk_users_mobile_verified_timestamp CHECK (
        (is_mobile_verified = FALSE AND mobile_verified_at IS NULL)
        OR (is_mobile_verified = TRUE AND mobile_verified_at IS NOT NULL)
    ),
    CONSTRAINT chk_users_email_index_hex CHECK (
        email_blind_index IS NULL OR email_blind_index ~ '^[a-f0-9]{64}$'
    )
);

CREATE TABLE otp_challenges (
    id UUID PRIMARY KEY,
    user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    target_blind_index CHAR(64) NOT NULL,
    channel VARCHAR(16) NOT NULL,
    purpose VARCHAR(24) NOT NULL,
    otp_code_hmac CHAR(64) NOT NULL,
    attempt_count SMALLINT NOT NULL DEFAULT 0,
    max_attempts SMALLINT NOT NULL DEFAULT 3,
    status VARCHAR(16) NOT NULL DEFAULT 'PENDING',
    client_ip_subnet_hash CHAR(64) NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    consumed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_otp_channel CHECK (channel IN ('MOBILE_SMS', 'EMAIL')),
    CONSTRAINT chk_otp_purpose CHECK (purpose IN ('LOGIN_OR_REGISTER', 'LINK_EMAIL', 'ACCOUNT_DELETE')),
    CONSTRAINT chk_otp_status CHECK (status IN ('PENDING', 'VERIFIED', 'EXPIRED', 'LOCKED')),
    CONSTRAINT chk_otp_attempts CHECK (attempt_count >= 0 AND max_attempts > 0 AND attempt_count <= max_attempts)
);

CREATE INDEX idx_otp_challenges_rate_limit
    ON otp_challenges (target_blind_index, created_at DESC);
CREATE INDEX idx_otp_challenges_ip_rate_limit
    ON otp_challenges (client_ip_subnet_hash, created_at DESC);

CREATE TABLE auth_sessions (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_family_id UUID NOT NULL,
    refresh_token_sha256 CHAR(64) NOT NULL UNIQUE,
    replaced_by_session_id UUID REFERENCES auth_sessions(id) ON DELETE SET NULL,
    device_fingerprint_hash CHAR(64) NOT NULL,
    platform VARCHAR(16) NOT NULL,
    revocation_reason VARCHAR(28),
    expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ,
    last_used_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_auth_sessions_platform CHECK (platform IN ('WEB', 'ANDROID', 'IOS')),
    CONSTRAINT chk_auth_sessions_revocation_reason CHECK (
        revocation_reason IS NULL OR revocation_reason IN (
            'USER_LOGOUT', 'TOKEN_ROTATED', 'TOKEN_REUSE_DETECTED', 'ADMIN_REVOKE', 'ACCOUNT_DELETED'
        )
    )
);

CREATE INDEX idx_auth_sessions_user_active
    ON auth_sessions (user_id, expires_at) WHERE revoked_at IS NULL;
CREATE INDEX idx_auth_sessions_family
    ON auth_sessions (token_family_id);

CREATE TABLE deleted_account_quotas (
    mobile_blind_index CHAR(64) PRIMARY KEY,
    blind_index_key_version SMALLINT NOT NULL DEFAULT 1,
    free_quota_consumed_total SMALLINT NOT NULL,
    deleted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT chk_deleted_quota_hex CHECK (mobile_blind_index ~ '^[a-f0-9]{64}$'),
    CONSTRAINT chk_deleted_quota_bounds CHECK (free_quota_consumed_total >= 0),
    CONSTRAINT chk_deleted_quota_expiry CHECK (expires_at > deleted_at)
);

CREATE INDEX idx_deleted_account_quotas_expires_at
    ON deleted_account_quotas (expires_at);

-- =============================================================================
-- MODULE 2: GOVERNMENT SOURCES & VERSIONED TEMPLATES (Tables 5 - 10)
-- =============================================================================

CREATE TABLE approved_source_domains (
    domain_pattern VARCHAR(120) PRIMARY KEY,
    domain_type VARCHAR(32) NOT NULL,
    allow_subdomains BOOLEAN NOT NULL DEFAULT TRUE,
    rationale VARCHAR(200) NOT NULL,
    approved_by_admin_id UUID REFERENCES users(id) ON DELETE SET NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_domain_pattern_lowercase CHECK (domain_pattern = LOWER(domain_pattern)),
    CONSTRAINT chk_domain_type CHECK (domain_type IN ('GOV_TLD_SUFFIX', 'STATUTORY_EXACT_HOST'))
);

CREATE TABLE government_sources (
    id UUID PRIMARY KEY,
    source_code VARCHAR(64) NOT NULL UNIQUE,
    jurisdiction VARCHAR(32) NOT NULL,
    ministry_or_department VARCHAR(160) NOT NULL,
    official_portal_url VARCHAR(512) NOT NULL,
    matched_domain_pattern VARCHAR(120) NOT NULL REFERENCES approved_source_domains(domain_pattern),
    source_type VARCHAR(32) NOT NULL,
    allow_automated_checksum_check BOOLEAN NOT NULL DEFAULT FALSE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    last_verified_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_gov_sources_jurisdiction CHECK (jurisdiction IN ('CENTRAL_INDIA', 'KARNATAKA_STATE')),
    CONSTRAINT chk_gov_sources_type CHECK (source_type IN ('OFFICIAL_GOV_PORTAL', 'STATUTORY_BODY')),
    CONSTRAINT chk_gov_sources_https CHECK (official_portal_url ~ '^https://[a-z0-9.-]+(/.*)?$')
);

-- Shared function to validate any official URL against approved_source_domains
CREATE OR REPLACE FUNCTION fn_validate_url_against_approved_domains(
    p_url VARCHAR,
    p_domain_pattern VARCHAR
) RETURNS BOOLEAN AS $$
DECLARE
    v_host TEXT;
    v_domain RECORD;
BEGIN
    v_host := LOWER(SUBSTRING(p_url FROM '^https://([^/:]+)'));
    IF v_host IS NULL THEN
        RETURN FALSE;
    END IF;

    SELECT domain_pattern, allow_subdomains, is_active
    INTO v_domain
    FROM approved_source_domains
    WHERE domain_pattern = p_domain_pattern;

    IF NOT FOUND OR NOT v_domain.is_active THEN
        RETURN FALSE;
    END IF;

    IF v_host = v_domain.domain_pattern THEN
        RETURN TRUE;
    END IF;

    IF v_domain.allow_subdomains AND v_host LIKE ('%.' || v_domain.domain_pattern) THEN
        RETURN TRUE;
    END IF;

    RETURN FALSE;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION fn_trg_validate_gov_source_url()
RETURNS TRIGGER AS $$
BEGIN
    IF NOT fn_validate_url_against_approved_domains(NEW.official_portal_url, NEW.matched_domain_pattern) THEN
        RAISE EXCEPTION 'official_portal_url (%) does not match active approved domain pattern (%)',
            NEW.official_portal_url, NEW.matched_domain_pattern;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_validate_gov_source_url
BEFORE INSERT OR UPDATE ON government_sources
FOR EACH ROW EXECUTE FUNCTION fn_trg_validate_gov_source_url();

CREATE TABLE form_templates (
    id UUID PRIMARY KEY,
    government_source_id UUID NOT NULL REFERENCES government_sources(id) ON DELETE RESTRICT,
    slug VARCHAR(80) NOT NULL UNIQUE,
    jurisdiction VARCHAR(32) NOT NULL,
    official_form_code VARCHAR(64) NOT NULL,
    category VARCHAR(32) NOT NULL,
    localized_titles JSONB NOT NULL,
    localized_descriptions JSONB NOT NULL,
    current_published_version_id UUID,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_form_templates_slug CHECK (slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$'),
    CONSTRAINT chk_form_templates_jurisdiction CHECK (jurisdiction IN ('CENTRAL_INDIA', 'KARNATAKA_STATE')),
    CONSTRAINT chk_form_templates_category CHECK (
        category IN ('REVENUE', 'IDENTITY', 'PENSION', 'TRANSPORT', 'WELFARE', 'EMPLOYMENT', 'RTI')
    )
);

CREATE TABLE template_versions (
    id UUID PRIMARY KEY,
    template_id UUID NOT NULL REFERENCES form_templates(id) ON DELETE RESTRICT,
    semver VARCHAR(20) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'DRAFT',
    source_pdf_download_url VARCHAR(512) NOT NULL,
    source_url_matched_domain VARCHAR(120) NOT NULL REFERENCES approved_source_domains(domain_pattern),
    source_downloaded_at TIMESTAMPTZ NOT NULL,
    blank_pdf_object_key VARCHAR(512) NOT NULL,
    blank_pdf_sha256 CHAR(64) NOT NULL,
    blank_pdf_size_bytes INTEGER NOT NULL,
    page_count SMALLINT NOT NULL,
    field_mapping_manifest JSONB NOT NULL,
    manifest_sha256 CHAR(64) NOT NULL,
    bundle_sha256 CHAR(64) NOT NULL,
    publisher_ed25519_sig VARCHAR(256),
    signing_key_id VARCHAR(32),
    created_by_admin_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    verified_by_admin_id UUID REFERENCES users(id) ON DELETE RESTRICT,
    approved_by_admin_id UUID REFERENCES users(id) ON DELETE RESTRICT,
    verified_at TIMESTAMPTZ,
    approved_at TIMESTAMPTZ,
    published_at TIMESTAMPTZ,
    deprecated_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_template_versions_semver UNIQUE (template_id, semver),
    CONSTRAINT chk_template_versions_semver_fmt CHECK (semver ~ '^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$'),
    CONSTRAINT chk_template_versions_status CHECK (
        status IN ('DRAFT', 'IN_REVIEW', 'VERIFIED', 'APPROVED', 'PUBLISHED', 'DEPRECATED')
    ),
    CONSTRAINT chk_template_versions_source_https CHECK (
        source_pdf_download_url ~ '^https://[a-z0-9.-]+(/.*)?$'
    ),
    CONSTRAINT chk_template_versions_pdf_bounds CHECK (
        blank_pdf_size_bytes > 0 AND blank_pdf_size_bytes <= 10485760
        AND page_count >= 1 AND page_count <= 50
        AND blank_pdf_sha256 ~ '^[a-f0-9]{64}$'
        AND manifest_sha256 ~ '^[a-f0-9]{64}$'
        AND bundle_sha256 ~ '^[a-f0-9]{64}$'
    ),
    CONSTRAINT chk_maker_checker_separation CHECK (
        (status NOT IN ('VERIFIED', 'APPROVED', 'PUBLISHED') OR (
            verified_by_admin_id IS NOT NULL AND verified_by_admin_id != created_by_admin_id
        ))
        AND (status NOT IN ('APPROVED', 'PUBLISHED') OR (
            approved_by_admin_id IS NOT NULL
            AND approved_by_admin_id != created_by_admin_id
            AND approved_by_admin_id != verified_by_admin_id
        ))
        AND (status != 'PUBLISHED' OR (
            publisher_ed25519_sig IS NOT NULL
            AND signing_key_id IS NOT NULL
            AND published_at IS NOT NULL
        ))
    )
);

CREATE OR REPLACE FUNCTION fn_trg_validate_template_version_source_url()
RETURNS TRIGGER AS $$
BEGIN
    IF NOT fn_validate_url_against_approved_domains(NEW.source_pdf_download_url, NEW.source_url_matched_domain) THEN
        RAISE EXCEPTION 'source_pdf_download_url (%) does not match active approved domain pattern (%)',
            NEW.source_pdf_download_url, NEW.source_url_matched_domain;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_validate_template_version_source_url
BEFORE INSERT OR UPDATE ON template_versions
FOR EACH ROW EXECUTE FUNCTION fn_trg_validate_template_version_source_url();

ALTER TABLE form_templates
    ADD CONSTRAINT fk_form_templates_current_published_version
    FOREIGN KEY (current_published_version_id) REFERENCES template_versions(id) ON DELETE SET NULL;

-- Immutable published template versions
CREATE OR REPLACE FUNCTION fn_prevent_published_template_mutation()
RETURNS TRIGGER AS $$
BEGIN
    IF OLD.status IN ('PUBLISHED', 'DEPRECATED') THEN
        IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'Cannot delete a PUBLISHED or DEPRECATED template version (id=%)', OLD.id;
        END IF;
        IF NEW.template_id != OLD.template_id
           OR NEW.semver != OLD.semver
           OR NEW.source_pdf_download_url != OLD.source_pdf_download_url
           OR NEW.source_url_matched_domain != OLD.source_url_matched_domain
           OR NEW.blank_pdf_object_key != OLD.blank_pdf_object_key
           OR NEW.blank_pdf_sha256 != OLD.blank_pdf_sha256
           OR NEW.blank_pdf_size_bytes != OLD.blank_pdf_size_bytes
           OR NEW.page_count != OLD.page_count
           OR NEW.field_mapping_manifest != OLD.field_mapping_manifest
           OR NEW.manifest_sha256 != OLD.manifest_sha256
           OR NEW.bundle_sha256 != OLD.bundle_sha256
           OR NEW.publisher_ed25519_sig IS DISTINCT FROM OLD.publisher_ed25519_sig
           OR NEW.signing_key_id IS DISTINCT FROM OLD.signing_key_id
           OR NEW.created_by_admin_id != OLD.created_by_admin_id
           OR NEW.verified_by_admin_id != OLD.verified_by_admin_id
           OR NEW.approved_by_admin_id != OLD.approved_by_admin_id THEN
            RAISE EXCEPTION 'Published template version artifacts and provenance are immutable (id=%)', OLD.id;
        END IF;
        IF OLD.status = 'PUBLISHED' AND NEW.status NOT IN ('PUBLISHED', 'DEPRECATED') THEN
            RAISE EXCEPTION 'Published template version can only transition to DEPRECATED';
        END IF;
        IF OLD.status = 'DEPRECATED' AND NEW.status != 'DEPRECATED' THEN
            RAISE EXCEPTION 'Deprecated template version cannot be reactivated';
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_enforce_published_template_immutability
BEFORE UPDATE OR DELETE ON template_versions
FOR EACH ROW EXECUTE FUNCTION fn_prevent_published_template_mutation();

CREATE TABLE government_source_checks (
    id UUID PRIMARY KEY,
    government_source_id UUID NOT NULL REFERENCES government_sources(id) ON DELETE CASCADE,
    template_version_id UUID REFERENCES template_versions(id) ON DELETE SET NULL,
    checked_url VARCHAR(512) NOT NULL,
    check_method VARCHAR(32) NOT NULL,
    http_status_code SMALLINT,
    etag_header VARCHAR(128),
    last_modified_header VARCHAR(128),
    observed_pdf_sha256 CHAR(64),
    check_result VARCHAR(24) NOT NULL,
    checked_by_admin_id UUID REFERENCES users(id) ON DELETE SET NULL,
    checked_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_source_check_method CHECK (
        check_method IN ('ADMIN_MANUAL_UPLOAD', 'ADMIN_ASSISTED_HEAD', 'SCHEDULED_STATIC_PDF_CHECK')
    ),
    CONSTRAINT chk_source_check_result CHECK (
        check_result IN ('INITIAL_INGEST', 'UNCHANGED', 'CHECKSUM_CHANGED', 'HTTP_ERROR')
    ),
    CONSTRAINT chk_source_check_sha256 CHECK (
        observed_pdf_sha256 IS NULL OR observed_pdf_sha256 ~ '^[a-f0-9]{64}$'
    )
);

CREATE INDEX idx_gov_source_checks_source_time
    ON government_source_checks (government_source_id, checked_at DESC);

CREATE TABLE template_approval_logs (
    id UUID PRIMARY KEY,
    template_version_id UUID NOT NULL REFERENCES template_versions(id) ON DELETE RESTRICT,
    actor_admin_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    from_status VARCHAR(20) NOT NULL,
    to_status VARCHAR(20) NOT NULL,
    recorded_bundle_sha256 CHAR(64) NOT NULL,
    review_notes TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_approval_logs_sha256 CHECK (recorded_bundle_sha256 ~ '^[a-f0-9]{64}$')
);

CREATE INDEX idx_template_approval_logs_version
    ON template_approval_logs (template_version_id, created_at ASC);

-- =============================================================================
-- MODULE 3 & 4: PRICING, PAYMENTS, REFUNDS & ENTITLEMENTS (Tables 11 - 17)
-- =============================================================================

CREATE TABLE pricing_catalog (
    sku_code VARCHAR(40) PRIMARY KEY,
    display_name VARCHAR(80) NOT NULL,
    credit_bucket VARCHAR(32) NOT NULL,
    base_amount_paise INTEGER NOT NULL,
    esp_surcharge_paise INTEGER NOT NULL DEFAULT 0,
    gst_rate_bps SMALLINT NOT NULL DEFAULT 1800,
    is_gst_inclusive BOOLEAN NOT NULL DEFAULT TRUE,
    total_payable_paise INTEGER NOT NULL,
    hsn_sac_code VARCHAR(16) NOT NULL DEFAULT '998314',
    currency VARCHAR(3) NOT NULL DEFAULT 'INR',
    esign_provider_code VARCHAR(40),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    effective_from TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    effective_until TIMESTAMPTZ,
    CONSTRAINT chk_pricing_credit_bucket CHECK (
        credit_bucket IN ('STANDARD_BASE', 'SIGNATURE_BUNDLE', 'SIGNATURE_UPGRADE', 'ESIGN_ADDON')
    ),
    CONSTRAINT chk_pricing_amounts_positive CHECK (
        base_amount_paise > 0 AND esp_surcharge_paise >= 0
        AND gst_rate_bps >= 0 AND gst_rate_bps <= 5000
        AND total_payable_paise >= base_amount_paise + esp_surcharge_paise
        AND currency = 'INR'
    )
);

CREATE TABLE payment_orders (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    sku_code VARCHAR(40) NOT NULL REFERENCES pricing_catalog(sku_code) ON DELETE RESTRICT,
    quantity SMALLINT NOT NULL DEFAULT 1,
    base_amount_paise INTEGER NOT NULL,
    esp_surcharge_paise INTEGER NOT NULL DEFAULT 0,
    tax_amount_paise INTEGER NOT NULL DEFAULT 0,
    total_amount_paise INTEGER NOT NULL,
    refunded_amount_paise INTEGER NOT NULL DEFAULT 0,
    currency VARCHAR(3) NOT NULL DEFAULT 'INR',
    gateway_provider VARCHAR(24) NOT NULL DEFAULT 'RAZORPAY',
    gateway_order_id VARCHAR(80) NOT NULL UNIQUE,
    gateway_payment_id VARCHAR(80) UNIQUE,
    payment_status VARCHAR(32) NOT NULL DEFAULT 'CREATED',
    idempotency_key VARCHAR(64) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    paid_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_payment_orders_user_idempotency UNIQUE (user_id, idempotency_key),
    CONSTRAINT chk_payment_orders_quantity CHECK (quantity = 1),
    CONSTRAINT chk_payment_orders_amounts CHECK (
        base_amount_paise > 0 AND esp_surcharge_paise >= 0 AND tax_amount_paise >= 0
        AND total_amount_paise = base_amount_paise + esp_surcharge_paise + tax_amount_paise
        AND refunded_amount_paise >= 0 AND refunded_amount_paise <= total_amount_paise
    ),
    CONSTRAINT chk_payment_orders_status CHECK (
        payment_status IN (
            'CREATED', 'AUTHORIZED', 'CAPTURED', 'FAILED',
            'PARTIALLY_REFUNDED', 'REFUNDED', 'REFUNDED_POST_CONSUMPTION'
        )
    )
);

CREATE INDEX idx_payment_orders_user_created
    ON payment_orders (user_id, created_at DESC);

CREATE TABLE entitlement_wallets (
    user_id UUID PRIMARY KEY REFERENCES users(id) ON DELETE RESTRICT,
    free_quota_total SMALLINT NOT NULL,
    free_quota_used SMALLINT NOT NULL DEFAULT 0,
    free_quota_reserved SMALLINT NOT NULL DEFAULT 0,
    paid_std_base_available INTEGER NOT NULL DEFAULT 0,
    paid_std_base_reserved INTEGER NOT NULL DEFAULT 0,
    paid_sig_bundle_available INTEGER NOT NULL DEFAULT 0,
    paid_sig_bundle_reserved INTEGER NOT NULL DEFAULT 0,
    paid_sig_upgrade_available INTEGER NOT NULL DEFAULT 0,
    paid_sig_upgrade_reserved INTEGER NOT NULL DEFAULT 0,
    esign_credits_by_sku JSONB NOT NULL DEFAULT '{}'::jsonb,
    post_consumption_refund_count SMALLINT NOT NULL DEFAULT 0,
    version_lock BIGINT NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_wallet_lifetime_free_bounds CHECK (
        free_quota_total >= 0
        AND free_quota_used >= 0
        AND free_quota_reserved >= 0
        AND (free_quota_used + free_quota_reserved) <= free_quota_total
    ),
    CONSTRAINT chk_wallet_paid_credits_non_negative CHECK (
        paid_std_base_available >= 0
        AND paid_std_base_reserved >= 0
        AND paid_sig_bundle_available >= 0
        AND paid_sig_bundle_reserved >= 0
        AND paid_sig_upgrade_available >= 0
        AND paid_sig_upgrade_reserved >= 0
        AND post_consumption_refund_count >= 0
    )
);

CREATE TABLE entitlement_leases (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    template_version_id UUID NOT NULL REFERENCES template_versions(id) ON DELETE RESTRICT,
    granted_tier VARCHAR(32) NOT NULL,
    base_credit_bucket VARCHAR(32) NOT NULL,
    is_upgraded_with_sig BOOLEAN NOT NULL DEFAULT FALSE,
    lease_status VARCHAR(24) NOT NULL DEFAULT 'ISSUED',
    device_fingerprint_hash CHAR(64) NOT NULL,
    draft_instance_id_hash CHAR(64) NOT NULL,
    jwt_jti VARCHAR(128) NOT NULL UNIQUE,
    signing_key_id VARCHAR(32) NOT NULL,
    client_completion_receipt_hash CHAR(64),
    commit_count SMALLINT NOT NULL DEFAULT 0,
    create_idempotency_key VARCHAR(64) NOT NULL,
    issued_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    reservation_expires_at TIMESTAMPTZ NOT NULL,
    first_committed_at TIMESTAMPTZ,
    correction_window_expires_at TIMESTAMPTZ,
    last_committed_at TIMESTAMPTZ,
    client_reported_completed_at TIMESTAMPTZ,
    released_at TIMESTAMPTZ,
    CONSTRAINT uq_leases_user_create_idempotency UNIQUE (user_id, create_idempotency_key),
    CONSTRAINT chk_leases_granted_tier CHECK (
        granted_tier IN (
            'FREE_STANDARD', 'PAID_STANDARD', 'PAID_SIGNATURE',
            'FREE_UPGRADED_SIGNATURE', 'PAID_UPGRADED_SIGNATURE', 'PAID_ESIGN'
        )
    ),
    CONSTRAINT chk_leases_base_bucket CHECK (
        base_credit_bucket IN ('FREE_QUOTA', 'STANDARD_BASE', 'SIGNATURE_BUNDLE', 'ESIGN_ADDON')
    ),
    CONSTRAINT chk_leases_upgrade_consistency CHECK (
        (is_upgraded_with_sig = FALSE AND granted_tier IN ('FREE_STANDARD', 'PAID_STANDARD', 'PAID_SIGNATURE', 'PAID_ESIGN'))
        OR (is_upgraded_with_sig = TRUE AND granted_tier IN ('FREE_UPGRADED_SIGNATURE', 'PAID_UPGRADED_SIGNATURE'))
    ),
    CONSTRAINT chk_leases_status CHECK (
        lease_status IN ('ISSUED', 'COMMITTED', 'RELEASED', 'EXPIRED_RELEASED', 'REVOKED_REFUND')
    ),
    CONSTRAINT chk_leases_hashes_hex CHECK (
        device_fingerprint_hash ~ '^[a-f0-9]{64}$'
        AND draft_instance_id_hash ~ '^[a-f0-9]{64}$'
        AND (client_completion_receipt_hash IS NULL OR client_completion_receipt_hash ~ '^[a-f0-9]{64}$')
    ),
    CONSTRAINT chk_leases_commit_state CHECK (
        (lease_status = 'COMMITTED' AND commit_count >= 1 AND first_committed_at IS NOT NULL AND correction_window_expires_at IS NOT NULL)
        OR (lease_status != 'COMMITTED' AND commit_count = 0 AND first_committed_at IS NULL)
    )
);

CREATE UNIQUE INDEX uq_active_lease_per_draft
    ON entitlement_leases (user_id, template_version_id, draft_instance_id_hash)
    WHERE lease_status IN ('ISSUED', 'COMMITTED');

CREATE INDEX idx_leases_reaper_expired
    ON entitlement_leases (reservation_expires_at)
    WHERE lease_status = 'ISSUED';

CREATE TABLE payment_refunds (
    id UUID PRIMARY KEY,
    payment_order_id UUID NOT NULL REFERENCES payment_orders(id) ON DELETE RESTRICT,
    gateway_refund_id VARCHAR(80) NOT NULL UNIQUE,
    refund_amount_paise INTEGER NOT NULL,
    base_component_refunded_paise INTEGER NOT NULL DEFAULT 0,
    surcharge_component_refunded_paise INTEGER NOT NULL DEFAULT 0,
    tax_component_refunded_paise INTEGER NOT NULL DEFAULT 0,
    refund_status VARCHAR(20) NOT NULL,
    wallet_reconciliation_action VARCHAR(32) NOT NULL DEFAULT 'NONE',
    revoked_lease_id UUID REFERENCES entitlement_leases(id) ON DELETE SET NULL,
    failure_reason VARCHAR(160),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    processed_at TIMESTAMPTZ,
    CONSTRAINT chk_payment_refunds_amount CHECK (
        refund_amount_paise > 0
        AND base_component_refunded_paise >= 0
        AND surcharge_component_refunded_paise >= 0
        AND tax_component_refunded_paise >= 0
        AND refund_amount_paise = base_component_refunded_paise + surcharge_component_refunded_paise + tax_component_refunded_paise
    ),
    CONSTRAINT chk_payment_refunds_status CHECK (refund_status IN ('INITIATED', 'PROCESSED', 'FAILED')),
    CONSTRAINT chk_payment_refunds_action CHECK (
        wallet_reconciliation_action IN (
            'NONE', 'PARTIAL_SURCHARGE_ADJUSTMENT', 'DEBITED_AVAILABLE_CREDIT',
            'REVOKED_ISSUED_LEASE', 'FLAGGED_POST_CONSUMPTION'
        )
    )
);

CREATE TABLE payment_webhook_events (
    gateway_event_id VARCHAR(100) PRIMARY KEY,
    gateway_provider VARCHAR(24) NOT NULL DEFAULT 'RAZORPAY',
    event_type VARCHAR(64) NOT NULL,
    gateway_order_id VARCHAR(80),
    payload_sha256 CHAR(64) NOT NULL,
    processing_outcome VARCHAR(32) NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_webhook_outcome CHECK (
        processing_outcome IN ('PROCESSED', 'IGNORED_DUPLICATE', 'IGNORED_UNTRACKED_EVENT')
    )
);

CREATE INDEX idx_payment_webhook_events_received_at
    ON payment_webhook_events (received_at);

CREATE TABLE entitlement_ledger_entries (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    sequence_no BIGINT NOT NULL,
    entry_type VARCHAR(32) NOT NULL,
    credit_bucket VARCHAR(32) NOT NULL,
    delta_available SMALLINT NOT NULL,
    delta_reserved SMALLINT NOT NULL,
    available_balance_after INTEGER NOT NULL,
    reserved_balance_after INTEGER NOT NULL,
    reference_payment_order_id UUID REFERENCES payment_orders(id) ON DELETE RESTRICT,
    reference_refund_id UUID REFERENCES payment_refunds(id) ON DELETE RESTRICT,
    reference_lease_id UUID REFERENCES entitlement_leases(id) ON DELETE RESTRICT,
    idempotency_key VARCHAR(64) NOT NULL,
    prev_entry_hash CHAR(64) NOT NULL,
    entry_hash CHAR(64) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_ledger_user_seq UNIQUE (user_id, sequence_no),
    CONSTRAINT uq_ledger_user_idempotency UNIQUE (user_id, idempotency_key),
    CONSTRAINT chk_ledger_entry_type CHECK (
        entry_type IN (
            'FREE_GRANT', 'FREE_RESTORE_DEDUCT', 'LEASE_RESERVE', 'LEASE_COMMIT',
            'LEASE_LATE_RECONCILE_COMMIT', 'LEASE_RELEASE', 'LEASE_EXPIRED_RELEASE',
            'LEASE_UPGRADE_RESERVE', 'LEASE_UPGRADE_COMMIT', 'PAID_CREDIT',
            'REFUND_DEBIT_AVAILABLE', 'REFUND_REVOKE_RESERVED',
            'REFUND_POST_CONSUMPTION', 'REFUND_PARTIAL_SURCHARGE_ONLY'
        )
    ),
    CONSTRAINT chk_ledger_credit_bucket CHECK (
        credit_bucket IN ('FREE_QUOTA', 'STANDARD_BASE', 'SIGNATURE_BUNDLE', 'SIGNATURE_UPGRADE', 'ESIGN_ADDON')
    ),
    CONSTRAINT chk_ledger_balances_non_negative CHECK (
        available_balance_after >= 0 AND reserved_balance_after >= 0
    ),
    CONSTRAINT chk_ledger_hashes_hex CHECK (
        prev_entry_hash ~ '^[a-f0-9]{64}$' AND entry_hash ~ '^[a-f0-9]{64}$'
    )
);

CREATE UNIQUE INDEX uq_ledger_paid_credit_per_order
    ON entitlement_ledger_entries (reference_payment_order_id)
    WHERE entry_type = 'PAID_CREDIT';

-- =============================================================================
-- MODULE 5 & 6: ESIGN HASH SESSIONS (Table 18) & AUDIT LOGS (Table 19)
-- =============================================================================

CREATE TABLE esign_hash_sessions (
    id UUID PRIMARY KEY,
    lease_id UUID NOT NULL UNIQUE REFERENCES entitlement_leases(id) ON DELETE RESTRICT,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    esp_provider_code VARCHAR(40) NOT NULL,
    asp_txn_id VARCHAR(64) NOT NULL UNIQUE,
    document_byterange_sha256 CHAR(64) NOT NULL,
    generic_doc_info VARCHAR(50) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'INITIATED',
    detached_pkcs7_signature TEXT,
    esp_error_code VARCHAR(32),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    CONSTRAINT chk_esign_doc_sha256_hex CHECK (document_byterange_sha256 ~ '^[a-f0-9]{64}$'),
    CONSTRAINT chk_esign_status CHECK (
        status IN ('INITIATED', 'ESP_REDIRECTED', 'SIGNED', 'FAILED', 'EXPIRED')
    )
);

CREATE TABLE audit_logs (
    id UUID PRIMARY KEY,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    actor_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    actor_role VARCHAR(24) NOT NULL,
    event_type VARCHAR(64) NOT NULL,
    resource_type VARCHAR(32) NOT NULL,
    resource_id VARCHAR(64) NOT NULL,
    ip_subnet_hash CHAR(64),
    request_id VARCHAR(64) NOT NULL,
    metadata_jsonb JSONB NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT chk_audit_logs_subnet_hex CHECK (
        ip_subnet_hash IS NULL OR ip_subnet_hash ~ '^[a-f0-9]{64}$'
    )
);

CREATE INDEX idx_audit_logs_occurred_at ON audit_logs (occurred_at DESC);
CREATE INDEX idx_audit_logs_resource ON audit_logs (resource_type, resource_id, occurred_at DESC);
CREATE INDEX idx_audit_logs_actor ON audit_logs (actor_user_id, occurred_at DESC);

CREATE OR REPLACE FUNCTION fn_enforce_append_only_table()
RETURNS TRIGGER AS $$
BEGIN
    IF TG_TABLE_NAME = 'audit_logs' AND TG_OP = 'UPDATE' THEN
        IF OLD.ip_subnet_hash IS NOT NULL AND NEW.ip_subnet_hash IS NULL
           AND NEW.id = OLD.id
           AND NEW.occurred_at = OLD.occurred_at
           AND NEW.actor_user_id IS NOT DISTINCT FROM OLD.actor_user_id
           AND NEW.actor_role = OLD.actor_role
           AND NEW.event_type = OLD.event_type
           AND NEW.resource_type = OLD.resource_type
           AND NEW.resource_id = OLD.resource_id
           AND NEW.request_id = OLD.request_id
           AND NEW.metadata_jsonb = OLD.metadata_jsonb THEN
            RETURN NEW;
        END IF;
    END IF;
    RAISE EXCEPTION 'Table % is strictly append-only; UPDATE and DELETE are prohibited', TG_TABLE_NAME;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_audit_logs_append_only
BEFORE UPDATE OR DELETE ON audit_logs
FOR EACH ROW EXECUTE FUNCTION fn_enforce_append_only_table();

CREATE TRIGGER trg_entitlement_ledger_append_only
BEFORE UPDATE OR DELETE ON entitlement_ledger_entries
FOR EACH ROW EXECUTE FUNCTION fn_enforce_append_only_table();

CREATE TRIGGER trg_template_approval_logs_append_only
BEFORE UPDATE OR DELETE ON template_approval_logs
FOR EACH ROW EXECUTE FUNCTION fn_enforce_append_only_table();
