-- ROBOport v0.6 / Customer account integrity (DESIGN DRAFT)
-- Apply ONLY on a fresh disposable PostgreSQL DB after migrations 001..004.
-- Database guards supplement, but DO NOT REPLACE, server-side RBAC/ABAC, authentic
-- IdP assertions, revocation of live sessions, or a legal data-retention policy.
BEGIN;

-- The B2C MVP intentionally models one active customer principal per portal account.
-- Organization-wide delegation/multi-account roles require a separate future ADR.
CREATE UNIQUE INDEX account_customer_link_one_active_customer
  ON account_customer_link (customer_id) WHERE revoked_at IS NULL;
CREATE UNIQUE INDEX account_customer_link_one_active_account
  ON account_customer_link (account_id) WHERE revoked_at IS NULL;

-- Re-assigning an already verified IdP identity is forbidden. Disable it, then
-- create a new verified identity through a reviewed account-recovery workflow.
CREATE OR REPLACE FUNCTION ro_identity_immutable_guard()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'RO_IDENTITY_DELETE_FORBIDDEN' USING ERRCODE='23514';
  END IF;
  IF (NEW.id,NEW.account_id,NEW.provider,NEW.subject_id,NEW.verified_at,NEW.created_at)
      IS DISTINCT FROM
     (OLD.id,OLD.account_id,OLD.provider,OLD.subject_id,OLD.verified_at,OLD.created_at) THEN
    RAISE EXCEPTION 'RO_IDENTITY_IMMUTABLE' USING ERRCODE='23514';
  END IF;
  IF OLD.disabled_at IS NOT NULL AND NEW.disabled_at IS DISTINCT FROM OLD.disabled_at THEN
    RAISE EXCEPTION 'RO_IDENTITY_REACTIVATION_FORBIDDEN' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END;$$;
CREATE TRIGGER trg_ro_identity_immutable
  BEFORE UPDATE OR DELETE ON account_identity
  FOR EACH ROW EXECUTE FUNCTION ro_identity_immutable_guard();

-- Verified ownership links cannot be retargeted. Revocation is the sole mutation;
-- a new link may be created only with fresh independently verified evidence.
CREATE OR REPLACE FUNCTION ro_account_link_guard()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'RO_LINK_DELETE_FORBIDDEN' USING ERRCODE='23514';
  END IF;
  IF (NEW.id,NEW.account_id,NEW.customer_id,NEW.verification_method,
      NEW.evidence_reference,NEW.verified_at,NEW.created_at)
      IS DISTINCT FROM
     (OLD.id,OLD.account_id,OLD.customer_id,OLD.verification_method,
      OLD.evidence_reference,OLD.verified_at,OLD.created_at) THEN
    RAISE EXCEPTION 'RO_LINK_IDENTITY_IMMUTABLE' USING ERRCODE='23514';
  END IF;
  IF OLD.revoked_at IS NOT NULL THEN
    IF (NEW.revoked_at,NEW.revoked_reason) IS DISTINCT FROM
       (OLD.revoked_at,OLD.revoked_reason) THEN
      RAISE EXCEPTION 'RO_LINK_REACTIVATION_FORBIDDEN' USING ERRCODE='23514';
    END IF;
  ELSIF NEW.revoked_at IS NOT NULL THEN
    IF NEW.revoked_reason IS NULL OR length(btrim(NEW.revoked_reason)) = 0 THEN
      RAISE EXCEPTION 'RO_LINK_REVOCATION_REASON_REQUIRED' USING ERRCODE='23514';
    END IF;
  ELSIF NEW.revoked_reason IS DISTINCT FROM OLD.revoked_reason THEN
    RAISE EXCEPTION 'RO_LINK_REASON_WITHOUT_REVOCATION' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END;$$;
CREATE TRIGGER trg_ro_account_link_guard
  BEFORE UPDATE OR DELETE ON account_customer_link
  FOR EACH ROW EXECUTE FUNCTION ro_account_link_guard();

-- A newly configured marketing preference must not be implicitly enabled.
ALTER TABLE account_notification_preference ALTER COLUMN enabled SET DEFAULT false;
ALTER TABLE account_notification_preference
  ADD CONSTRAINT ro_marketing_consent_nonblank_ck
    CHECK (purpose <> 'marketing' OR NOT enabled OR
           (consent_version IS NOT NULL AND length(btrim(consent_version)) > 0));

-- Document access events are an immutable RECORD OF A COMPLETED ACCESS, not the
-- authorization layer for raw object storage or attachment downloads.
CREATE OR REPLACE FUNCTION ro_customer_document_access_guard()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_ticket uuid; v_customer uuid; v_allowed boolean;
BEGIN
  IF TG_OP <> 'INSERT' THEN
    RAISE EXCEPTION 'RO_DOCUMENT_AUDIT_IMMUTABLE' USING ERRCODE='23514';
  END IF;
  SELECT a.ticket_id INTO v_ticket FROM attachment a WHERE a.id=NEW.attachment_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'RO_DOCUMENT_NOT_FOUND' USING ERRCODE='23503';
  END IF;
  IF NEW.action IN ('READ','DOWNLOAD') THEN
    IF v_ticket IS NULL OR NEW.ticket_id IS DISTINCT FROM v_ticket THEN
      RAISE EXCEPTION 'RO_DOCUMENT_TICKET_MISMATCH' USING ERRCODE='23514';
    END IF;
    SELECT t.customer_id INTO v_customer FROM service_ticket t WHERE t.id=v_ticket;
    SELECT EXISTS(
      SELECT 1 FROM account_customer_link l
      JOIN customer_account ca ON ca.id=l.account_id
      JOIN account_identity ai ON ai.account_id=ca.id
      WHERE l.account_id=NEW.account_id AND l.customer_id=v_customer
        AND l.revoked_at IS NULL AND ca.account_status='active'
        AND ai.disabled_at IS NULL
    ) INTO v_allowed;
    IF NOT v_allowed THEN
      RAISE EXCEPTION 'RO_DOCUMENT_ACCOUNT_NOT_AUTHORIZED' USING ERRCODE='23514';
    END IF;
  ELSIF NEW.ticket_id IS NOT NULL AND NEW.ticket_id IS DISTINCT FROM v_ticket THEN
    RAISE EXCEPTION 'RO_DOCUMENT_TICKET_MISMATCH' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END;$$;
CREATE TRIGGER trg_ro_customer_document_access_guard
  BEFORE INSERT OR UPDATE OR DELETE ON document_access_audit
  FOR EACH ROW EXECUTE FUNCTION ro_customer_document_access_guard();

-- CRITICAL LIMITATION: This guard protects recorded READ/DOWNLOAD events only.
-- It is NOT triggered by arbitrary SELECTs or S3/object-store access. Restrict
-- DB credentials, APIs and storage access using explicit per-request ABAC.
COMMIT;
