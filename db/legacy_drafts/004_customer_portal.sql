-- ROBOport 004 — Customer Portal domain (DESIGN DRAFT; run ONLY on fresh scratch PG)
-- Do not put credentials, OTP, claim tokens, or real PII into the database fixtures.
BEGIN;

CREATE TABLE customer_account (
  id uuid PRIMARY KEY,
  display_name text NOT NULL CHECK (length(btrim(display_name)) > 0),
  preferred_locale varchar(8) NOT NULL DEFAULT 'ru' CHECK (preferred_locale IN ('ru','en')),
  account_status text NOT NULL DEFAULT 'active' CHECK (account_status IN ('active','blocked','closed')),
  version bigint NOT NULL DEFAULT 1 CHECK (version >= 1),
  created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE customer_account IS 'Portal account is NOT a customer ownership record; authorization requires verified links';

CREATE TABLE account_identity (
  id uuid PRIMARY KEY,
  account_id uuid NOT NULL REFERENCES customer_account(id) ON DELETE RESTRICT,
  provider text NOT NULL CHECK (length(btrim(provider)) > 0),
  subject_id text NOT NULL CHECK (length(btrim(subject_id)) > 0),
  verified_at timestamptz NOT NULL,
  disabled_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (provider, subject_id),
  CHECK (disabled_at IS NULL OR disabled_at >= verified_at)
);
CREATE INDEX account_identity_account_idx ON account_identity(account_id);
COMMENT ON TABLE account_identity IS 'IdP subject only; no passwords/tokens stored here';

CREATE TABLE account_customer_link (
  id uuid PRIMARY KEY,
  account_id uuid NOT NULL REFERENCES customer_account(id) ON DELETE RESTRICT,
  customer_id uuid NOT NULL REFERENCES customer(id) ON DELETE RESTRICT,
  verification_method text NOT NULL CHECK (length(btrim(verification_method)) > 0),
  evidence_reference text NOT NULL CHECK (length(btrim(evidence_reference)) > 0),
  verified_at timestamptz NOT NULL,
  revoked_at timestamptz,
  revoked_reason text,
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (revoked_at IS NULL OR revoked_at >= verified_at),
  CHECK (revoked_at IS NULL OR revoked_reason IS NOT NULL)
);
CREATE UNIQUE INDEX account_customer_link_active_unique ON account_customer_link (account_id, customer_id)
  WHERE revoked_at IS NULL;
CREATE INDEX account_customer_link_customer_idx ON account_customer_link(customer_id)
  WHERE revoked_at IS NULL;
COMMENT ON TABLE account_customer_link IS 'Evidence must be validated by a trusted service before INSERT. NEVER infer ownership from phone, SN or order reference';

CREATE TABLE account_notification_preference (
  id uuid PRIMARY KEY,
  account_id uuid NOT NULL REFERENCES customer_account(id) ON DELETE RESTRICT,
  event_type text NOT NULL CHECK (length(btrim(event_type)) > 0),
  channel text NOT NULL CHECK (channel IN ('email','sms','in_app')),
  purpose text NOT NULL CHECK (purpose IN ('service','marketing')),
  enabled boolean NOT NULL DEFAULT true,
  consent_version text,
  changed_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(account_id,event_type,channel,purpose),
  CHECK (purpose <> 'marketing' OR (enabled = false OR consent_version IS NOT NULL))
);
COMMENT ON TABLE account_notification_preference IS 'No implied marketing opt-in; backend checks lawful basis for every communication';

CREATE TABLE document_access_audit (
  id uuid PRIMARY KEY,
  account_id uuid NOT NULL REFERENCES customer_account(id) ON DELETE RESTRICT,
  attachment_id uuid NOT NULL REFERENCES attachment(id) ON DELETE RESTRICT,
  ticket_id uuid REFERENCES service_ticket(id) ON DELETE RESTRICT,
  action text NOT NULL CHECK (action IN ('READ','DOWNLOAD','DENIED')),
  reason_code text,
  occurred_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX document_access_audit_account_idx ON document_access_audit(account_id,occurred_at DESC);
CREATE INDEX document_access_audit_attachment_idx ON document_access_audit(attachment_id,occurred_at DESC);
COMMENT ON TABLE document_access_audit IS 'Append-only by application policy; SQL-level immutability/role grants require reviewed migration';

-- Gate: keep all portal tables private until application-level subject + customer +
-- ticket/document ownership verification has been implemented, reviewed and tested.
-- An account link alone does not authorize access to tickets of previous device owners.
COMMIT;
