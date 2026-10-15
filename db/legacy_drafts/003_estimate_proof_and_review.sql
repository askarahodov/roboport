-- ROBOport v0.4: further draft guards. ONLY for an EMPTY DISPOSABLE test DB.
-- Depends on 001 and 002. Not executed on a live PostgreSQL server yet.
-- Does not replace server-side authorization, business workflow or independent security review.
BEGIN;

ALTER TABLE service_capability
  ADD COLUMN reviewed_by uuid REFERENCES staff_user(id),
  ADD CONSTRAINT service_capability_two_person_approval_ck
    CHECK (status <> 'approved' OR
      (reviewed_by IS NOT NULL AND approved_by IS NOT NULL
       AND reviewed_by <> approved_by));

-- Approval record must correspond to an already SENT immutable estimate,
-- be from the actual ticket's customer, and be immutable after insertion.
CREATE OR REPLACE FUNCTION ro_guard_estimate_approval()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_status text; v_customer uuid;
BEGIN
  IF TG_OP <> 'INSERT' THEN
    RAISE EXCEPTION 'RO_APPROVAL_IMMUTABLE' USING ERRCODE = '23514';
  END IF;
  SELECT e.status, t.customer_id INTO v_status, v_customer
  FROM estimate e JOIN service_ticket t ON t.id = e.ticket_id
  WHERE e.id = NEW.estimate_id FOR UPDATE OF e;
  IF v_status IS DISTINCT FROM 'SENT' OR v_customer IS DISTINCT FROM NEW.customer_id THEN
    RAISE EXCEPTION 'RO_APPROVAL_WRONG_ESTIMATE_OR_CUSTOMER' USING ERRCODE = '23514';
  END IF;
  IF length(trim(NEW.proof_reference)) = 0 THEN
    RAISE EXCEPTION 'RO_APPROVAL_PROOF_EMPTY' USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END;
$$;
CREATE TRIGGER trg_estimate_approval_guard
  BEFORE INSERT OR UPDATE OR DELETE ON estimate_approval
  FOR EACH ROW EXECUTE FUNCTION ro_guard_estimate_approval();

-- This strengthens (replaces) v0.3's guard; do not re-create a duplicate trigger.
CREATE OR REPLACE FUNCTION ro_guard_frozen_estimate()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_expected_total numeric(14,2); v_approval_at timestamptz;
BEGIN
  IF OLD.status <> 'DRAFT' AND
    (NEW.ticket_id, NEW.version, NEW.currency, NEW.total_amount, NEW.sent_at, NEW.created_at)
      IS DISTINCT FROM
    (OLD.ticket_id, OLD.version, OLD.currency, OLD.total_amount, OLD.sent_at, OLD.created_at)
  THEN
    RAISE EXCEPTION 'RO_ESTIMATE_FROZEN' USING ERRCODE = '23514';
  END IF;

  IF OLD.status IN ('DECLINED','SUPERSEDED') AND NEW.status <> OLD.status THEN
    RAISE EXCEPTION 'RO_ESTIMATE_TERMINAL' USING ERRCODE = '23514';
  END IF;
  IF OLD.status = 'APPROVED' AND NEW.status NOT IN ('APPROVED','SUPERSEDED') THEN
    RAISE EXCEPTION 'RO_APPROVED_ESTIMATE_INVALID_TRANSITION' USING ERRCODE = '23514';
  END IF;
  IF OLD.status = 'SENT' AND NEW.status NOT IN ('SENT','APPROVED','DECLINED','SUPERSEDED') THEN
    RAISE EXCEPTION 'RO_SENT_ESTIMATE_INVALID_TRANSITION' USING ERRCODE = '23514';
  END IF;
  IF OLD.status = 'DRAFT' AND NEW.status NOT IN ('DRAFT','SENT') THEN
    RAISE EXCEPTION 'RO_ESTIMATE_NEEDS_SEND' USING ERRCODE = '23514';
  END IF;

  IF OLD.status = 'DRAFT' AND NEW.status = 'SENT' THEN
    IF (NEW.ticket_id, NEW.version, NEW.currency, NEW.total_amount, NEW.created_at)
        IS DISTINCT FROM
       (OLD.ticket_id, OLD.version, OLD.currency, OLD.total_amount, OLD.created_at) THEN
      RAISE EXCEPTION 'RO_ESTIMATE_EDIT_THEN_SEND' USING ERRCODE = '23514';
    END IF;
    IF NEW.sent_at IS NULL OR NEW.approved_at IS NOT NULL THEN
      RAISE EXCEPTION 'RO_SENT_TIMESTAMP_INVALID' USING ERRCODE = '23514';
    END IF;
    SELECT round(sum(l.quantity * l.unit_price_snapshot),2) INTO v_expected_total
      FROM estimate_line l WHERE l.estimate_id = OLD.id;
    IF v_expected_total IS NULL OR NEW.total_amount IS DISTINCT FROM v_expected_total THEN
      RAISE EXCEPTION 'RO_ESTIMATE_LINES_TOTAL_MISMATCH' USING ERRCODE = '23514';
    END IF;
  END IF;

  IF OLD.status = 'SENT' AND NEW.status = 'APPROVED' THEN
    SELECT a.approved_at INTO v_approval_at
      FROM estimate_approval a WHERE a.estimate_id = OLD.id;
    IF v_approval_at IS NULL OR NEW.approved_at IS DISTINCT FROM v_approval_at THEN
      RAISE EXCEPTION 'RO_ESTIMATE_NEEDS_VALID_APPROVAL' USING ERRCODE = '23514';
    END IF;
  ELSIF NEW.approved_at IS DISTINCT FROM OLD.approved_at THEN
    RAISE EXCEPTION 'RO_APPROVAL_TIMESTAMP_FROZEN' USING ERRCODE = '23514';
  END IF;

  RETURN NEW;
END;
$$;
COMMIT;
