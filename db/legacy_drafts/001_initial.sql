-- ROBOport / PostgreSQL initial logical schema v0.2 (DRAFT)
-- Synthetic data only. No real clients. Execute only in disposable isolated database.
-- UUID supplied by application; no reliance on extensions. Permission policies/RLS,
-- tamper-evident audit triggers, legal retention and PII cryptography need dedicated review.
BEGIN;

CREATE TABLE brand (
  id uuid PRIMARY KEY,
  display_name text NOT NULL CHECK (length(trim(display_name)) > 0),
  country_code varchar(2), official_url text,
  source_verified_at timestamptz, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX brand_display_name_ci ON brand (lower(display_name));
CREATE TABLE robot_model (
  id uuid PRIMARY KEY, brand_id uuid NOT NULL REFERENCES brand(id),
  name text NOT NULL CHECK (length(trim(name)) > 0),
  category text NOT NULL CHECK (category IN ('vacuum','window_cleaner','lawn_mower','pool_cleaner','companion','quadruped','humanoid','other')),
  support_status text NOT NULL DEFAULT 'candidate' CHECK (support_status IN ('candidate','evaluation','supported','restricted','not_supported','retired')),
  source_url text, created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (brand_id, name)
);
CREATE INDEX robot_model_brand_idx ON robot_model(brand_id);
CREATE TABLE dock_variant (
  id uuid PRIMARY KEY, model_id uuid NOT NULL REFERENCES robot_model(id),
  manufacturer_sku text, hardware_revision text, region_code varchar(16),
  mains_powered boolean NOT NULL DEFAULT false, water_system boolean NOT NULL DEFAULT false,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE model_variant (
  id uuid PRIMARY KEY, model_id uuid NOT NULL REFERENCES robot_model(id),
  manufacturer_sku text, region_code varchar(16), hardware_revision text,
  dock_variant_id uuid REFERENCES dock_variant(id), notes text,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX model_variant_model_idx ON model_variant(model_id);
CREATE TABLE service_operation (
  id uuid PRIMARY KEY, code varchar(64) NOT NULL UNIQUE,
  title text NOT NULL, risk_level text NOT NULL DEFAULT 'low'
    CHECK (risk_level IN ('low','medium','critical')),
  description text
);
CREATE TABLE service_capability (
  id uuid PRIMARY KEY, variant_id uuid NOT NULL REFERENCES model_variant(id),
  operation_id uuid NOT NULL REFERENCES service_operation(id),
  status text NOT NULL DEFAULT 'candidate'
    CHECK (status IN ('candidate','evaluation','approved','restricted','revoked')),
  evidence_uri text, approved_by uuid, approved_at timestamptz,
  notes text, UNIQUE(variant_id,operation_id),
  CHECK ((status <> 'approved') OR (evidence_uri IS NOT NULL AND approved_by IS NOT NULL AND approved_at IS NOT NULL))
);
CREATE TABLE customer (
  id uuid PRIMARY KEY, customer_type text NOT NULL CHECK (customer_type IN ('person','company')),
  display_name text NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE customer_contact (
  id uuid PRIMARY KEY, customer_id uuid NOT NULL REFERENCES customer(id),
  channel text NOT NULL CHECK (channel IN ('email','phone','other')),
  contact_value_ciphertext bytea NOT NULL, contact_lookup_hmac bytea,
  is_verified boolean NOT NULL DEFAULT false, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX customer_contact_customer_idx ON customer_contact(customer_id);
CREATE TABLE robot_device (
  id uuid PRIMARY KEY, variant_id uuid REFERENCES model_variant(id),
  serial_ciphertext bytea, serial_lookup_hmac bytea, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX robot_device_serial_lookup_idx ON robot_device(serial_lookup_hmac) WHERE serial_lookup_hmac IS NOT NULL;
CREATE TABLE device_ownership (
  id uuid PRIMARY KEY, device_id uuid NOT NULL REFERENCES robot_device(id),
  customer_id uuid NOT NULL REFERENCES customer(id),
  valid_from timestamptz NOT NULL DEFAULT now(), valid_to timestamptz,
  CHECK (valid_to IS NULL OR valid_to > valid_from)
);
CREATE INDEX device_ownership_device_idx ON device_ownership(device_id,valid_from);
CREATE TABLE staff_user (
  id uuid PRIMARY KEY, oidc_subject text NOT NULL UNIQUE,
  display_name text NOT NULL, is_active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE user_role (
  id uuid PRIMARY KEY, staff_user_id uuid NOT NULL REFERENCES staff_user(id),
  role_key text NOT NULL CHECK (role_key IN ('INTAKE','ENGINEER','STOCK','MANAGER','FINANCE','ADMIN','AUDITOR')),
  UNIQUE(staff_user_id,role_key)
);
CREATE TABLE service_ticket (
  id uuid PRIMARY KEY, public_reference varchar(40) NOT NULL UNIQUE,
  customer_id uuid NOT NULL REFERENCES customer(id),
  device_id uuid REFERENCES robot_device(id), assigned_staff_user_id uuid REFERENCES staff_user(id),
  source text NOT NULL DEFAULT 'walk_in' CHECK (source IN ('web','walk_in','partner')),
  symptoms text NOT NULL, safety_flags text[] NOT NULL DEFAULT '{}',
  state text NOT NULL DEFAULT 'DRAFT', version bigint NOT NULL DEFAULT 1 CHECK (version > 0),
  outcome text CHECK (outcome IN ('REPAIRED','UNREPAIRED','REFERRED')),
  created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
  CHECK (state IN ('DRAFT','SUBMITTED','TRIAGE','AWAITING_DELIVERY','RECEIVED',
     'DIAGNOSTICS','ESTIMATE_PENDING','AWAITING_APPROVAL','APPROVED','WAITING_PARTS',
     'IN_REPAIR','QC','READY_FOR_RETURN','COMPLETED','SAFETY_HOLD','WARRANTY_ROUTE',
     'DECLINED','UNREPAIRABLE','QC_FAILED','CANCELLED')),
  CHECK ((state <> 'COMPLETED') OR outcome IS NOT NULL)
);
CREATE INDEX service_ticket_customer_idx ON service_ticket(customer_id,created_at DESC);
CREATE INDEX service_ticket_state_idx ON service_ticket(state,created_at);
CREATE TABLE ticket_state_event (
  id uuid PRIMARY KEY, ticket_id uuid NOT NULL REFERENCES service_ticket(id),
  from_state text, to_state text NOT NULL, reason text NOT NULL,
  actor_staff_user_id uuid REFERENCES staff_user(id),
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ticket_state_event_ticket_idx ON ticket_state_event(ticket_id,created_at);
CREATE TABLE attachment (
  id uuid PRIMARY KEY, ticket_id uuid REFERENCES service_ticket(id),
  owner_type text NOT NULL, owner_id uuid NOT NULL,
  storage_key text NOT NULL UNIQUE, original_name text,
  mime_type text NOT NULL, size_bytes bigint NOT NULL CHECK (size_bytes >= 0),
  sha256_digest bytea, quarantined boolean NOT NULL DEFAULT true,
  retention_at timestamptz, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE intake_record (
  id uuid PRIMARY KEY, ticket_id uuid NOT NULL REFERENCES service_ticket(id),
  accessories text[] NOT NULL DEFAULT '{}', condition_notes text NOT NULL,
  signed_document_attachment_id uuid REFERENCES attachment(id),
  received_by uuid REFERENCES staff_user(id), received_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE diagnosis (
  id uuid PRIMARY KEY, ticket_id uuid NOT NULL REFERENCES service_ticket(id),
  engineer_id uuid REFERENCES staff_user(id), hypothesis text,
  confirmed_fault text, risk_level text NOT NULL CHECK (risk_level IN ('low','medium','critical')),
  observations text NOT NULL, labor_minutes integer NOT NULL DEFAULT 0 CHECK (labor_minutes >= 0),
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE part (
  id uuid PRIMARY KEY, manufacturer text NOT NULL, part_number text NOT NULL,
  revision text NOT NULL DEFAULT '', name text NOT NULL,
  origin_note text, created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (manufacturer,part_number,revision)
);
CREATE TABLE supplier (
  id uuid PRIMARY KEY, name text NOT NULL, website text,
  email_ciphertext bytea, phone_ciphertext bytea,
  verification_status text NOT NULL DEFAULT 'unverified'
    CHECK (verification_status IN ('unverified','contact_verified','commercially_verified','blocked')),
  source_uri text, verified_at timestamptz
);
CREATE TABLE supplier_quote (
  id uuid PRIMARY KEY, supplier_id uuid NOT NULL REFERENCES supplier(id),
  part_id uuid NOT NULL REFERENCES part(id),
  price numeric(14,2) NOT NULL CHECK (price >= 0), currency varchar(3) NOT NULL,
  minimum_order_quantity integer NOT NULL CHECK (minimum_order_quantity > 0),
  valid_until date, delivery_days integer CHECK (delivery_days >= 0),
  document_attachment_id uuid REFERENCES attachment(id),
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE part_compatibility (
  id uuid PRIMARY KEY, part_id uuid NOT NULL REFERENCES part(id),
  variant_id uuid NOT NULL REFERENCES model_variant(id), evidence_version integer NOT NULL DEFAULT 1,
  status text NOT NULL DEFAULT 'unverified'
    CHECK (status IN ('unverified','manufacturer_documented','technician_tested','incompatible','revoked')),
  evidence_uri text, verified_by uuid REFERENCES staff_user(id), reviewed_by uuid REFERENCES staff_user(id),
  verified_at timestamptz, created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(part_id,variant_id,evidence_version),
  CHECK (status NOT IN ('manufacturer_documented','technician_tested') OR
    (evidence_uri IS NOT NULL AND verified_by IS NOT NULL AND reviewed_by IS NOT NULL AND verified_at IS NOT NULL)),
  CHECK (reviewed_by IS DISTINCT FROM verified_by OR reviewed_by IS NULL)
);
CREATE TABLE stock_lot (
  id uuid PRIMARY KEY, part_id uuid NOT NULL REFERENCES part(id),
  supplier_id uuid REFERENCES supplier(id), batch_number text,
  qty_on_hand integer NOT NULL DEFAULT 0 CHECK (qty_on_hand >= 0),
  qty_reserved integer NOT NULL DEFAULT 0 CHECK (qty_reserved >= 0),
  cost numeric(14,2) NOT NULL DEFAULT 0 CHECK (cost >= 0), currency varchar(3) NOT NULL DEFAULT 'RUB',
  received_at timestamptz NOT NULL DEFAULT now(), CHECK (qty_reserved <= qty_on_hand)
);
CREATE INDEX stock_lot_part_idx ON stock_lot(part_id);
CREATE TABLE estimate (
  id uuid PRIMARY KEY, ticket_id uuid NOT NULL REFERENCES service_ticket(id),
  version integer NOT NULL CHECK (version >= 1),
  status text NOT NULL DEFAULT 'DRAFT'
    CHECK (status IN ('DRAFT','SENT','APPROVED','DECLINED','SUPERSEDED')),
  currency varchar(3) NOT NULL DEFAULT 'RUB',
  total_amount numeric(14,2) NOT NULL CHECK (total_amount >= 0),
  sent_at timestamptz, approved_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(ticket_id, version)
);
CREATE TABLE estimate_line (
  id uuid PRIMARY KEY, estimate_id uuid NOT NULL REFERENCES estimate(id),
  line_type text NOT NULL CHECK (line_type IN ('labor','part','logistics')),
  part_id uuid REFERENCES part(id), service_operation_id uuid REFERENCES service_operation(id),
  description text NOT NULL, quantity numeric(12,3) NOT NULL CHECK (quantity > 0),
  unit_price_snapshot numeric(14,2) NOT NULL CHECK (unit_price_snapshot >= 0),
  unit_cost_snapshot numeric(14,2) CHECK (unit_cost_snapshot >= 0),
  CHECK (line_type <> 'part' OR part_id IS NOT NULL)
);
CREATE TABLE estimate_approval (
  id uuid PRIMARY KEY, estimate_id uuid NOT NULL REFERENCES estimate(id),
  customer_id uuid NOT NULL REFERENCES customer(id),
  approval_method text NOT NULL, proof_reference text NOT NULL,
  approved_at timestamptz NOT NULL DEFAULT now(), UNIQUE(estimate_id)
);
CREATE TABLE work_order (
  id uuid PRIMARY KEY, ticket_id uuid NOT NULL REFERENCES service_ticket(id),
  engineer_id uuid NOT NULL REFERENCES staff_user(id),
  service_operation_id uuid NOT NULL REFERENCES service_operation(id),
  description text NOT NULL, labor_minutes integer NOT NULL CHECK (labor_minutes > 0),
  started_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz
);
CREATE TABLE quality_check (
  id uuid PRIMARY KEY, ticket_id uuid NOT NULL REFERENCES service_ticket(id),
  performed_by uuid NOT NULL REFERENCES staff_user(id),
  checklist_version text NOT NULL,
  result text NOT NULL CHECK (result IN ('PASS','FAIL','NOT_RUN')),
  evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE stock_reservation (
  id uuid PRIMARY KEY, stock_lot_id uuid NOT NULL REFERENCES stock_lot(id),
  ticket_id uuid NOT NULL REFERENCES service_ticket(id),
  qty integer NOT NULL CHECK (qty > 0),
  status text NOT NULL CHECK (status IN ('active','consumed','released')),
  created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE stock_movement (
  id uuid PRIMARY KEY, stock_lot_id uuid NOT NULL REFERENCES stock_lot(id),
  ticket_id uuid REFERENCES service_ticket(id), actor_id uuid REFERENCES staff_user(id),
  delta_qty integer NOT NULL CHECK (delta_qty <> 0),
  reason text NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE audit_event (
  id uuid PRIMARY KEY, actor_staff_user_id uuid REFERENCES staff_user(id),
  action text NOT NULL, entity_type text NOT NULL, entity_id uuid NOT NULL,
  safe_diff jsonb NOT NULL DEFAULT '{}'::jsonb, request_id uuid,
  occurred_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX audit_event_entity_idx ON audit_event(entity_type, entity_id, occurred_at);
CREATE TABLE notification_outbox (
  id uuid PRIMARY KEY, event_type text NOT NULL, aggregate_type text NOT NULL,
  aggregate_id uuid NOT NULL, payload jsonb NOT NULL,
  attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
  next_attempt_at timestamptz NOT NULL DEFAULT now(), delivered_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE idempotency_record (
  id uuid PRIMARY KEY, actor_scope text NOT NULL, key_hmac bytea NOT NULL,
  request_hash bytea NOT NULL, response_status integer NOT NULL,
  response_body jsonb, expires_at timestamptz NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(actor_scope,key_hmac)
);
CREATE TABLE customer_consent (
  id uuid PRIMARY KEY, customer_id uuid NOT NULL REFERENCES customer(id),
  ticket_id uuid REFERENCES service_ticket(id), consent_purpose text NOT NULL,
  legal_document_version text NOT NULL,
  approved_at timestamptz NOT NULL DEFAULT now(), proof_reference text
);
CREATE TABLE status_access_token (
  id uuid PRIMARY KEY, ticket_id uuid NOT NULL REFERENCES service_ticket(id),
  token_hash bytea NOT NULL UNIQUE, scope text NOT NULL,
  issued_at timestamptz NOT NULL DEFAULT now(),
  expires_at timestamptz NOT NULL, revoked_at timestamptz,
  CHECK (expires_at > issued_at)
);
CREATE INDEX status_access_token_ticket_idx ON status_access_token(ticket_id);
COMMIT;
