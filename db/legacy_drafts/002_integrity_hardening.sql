-- ROBOport / supplemental integrity controls v0.3 - DRAFT, no production use.
-- PostgreSQL 14+ syntax. Requires running 001_initial.sql first in an isolated database.
-- Prevents historical price mutation and multiple concurrent open ownerships.
-- Does NOT implement RBAC/ABAC, safe repair authorization or automatic stock reservation.
BEGIN;

ALTER TABLE service_capability
  ADD CONSTRAINT service_capability_approved_by_fkey
  FOREIGN KEY (approved_by) REFERENCES staff_user(id);

-- One current owner. Earlier ownership periods still require application-side
-- temporal validation; this partial index does not forbid ALL historical overlaps.
CREATE UNIQUE INDEX device_one_current_owner_idx
  ON device_ownership(device_id) WHERE valid_to IS NULL;

CREATE OR REPLACE FUNCTION ro_guard_frozen_estimate()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.status <> 'DRAFT' AND
     (NEW.ticket_id, NEW.version, NEW.currency, NEW.total_amount, NEW.sent_at, NEW.created_at)
       IS DISTINCT FROM
     (OLD.ticket_id, OLD.version, OLD.currency, OLD.total_amount, OLD.sent_at, OLD.created_at)
  THEN
    RAISE EXCEPTION 'RO_ESTIMATE_FROZEN' USING ERRCODE = '23514';
  END IF;
  IF OLD.status = 'APPROVED' AND NEW.status NOT IN ('APPROVED','SUPERSEDED') THEN
    RAISE EXCEPTION 'RO_ESTIMATE_APPROVED_IMMUTABLE' USING ERRCODE = '23514';
  END IF;
  IF OLD.status IN ('DECLINED','SUPERSEDED') AND NEW.status <> OLD.status THEN
    RAISE EXCEPTION 'RO_ESTIMATE_TERMINAL' USING ERRCODE = '23514';
  END IF;
  IF OLD.status = 'DRAFT' AND NEW.status NOT IN ('DRAFT','SENT') THEN
    RAISE EXCEPTION 'RO_ESTIMATE_NEEDS_SEND' USING ERRCODE = '23514';
  END IF;
  IF OLD.status = 'DRAFT' AND NEW.status = 'SENT' AND
    (NEW.ticket_id, NEW.version, NEW.currency, NEW.total_amount, NEW.created_at)
       IS DISTINCT FROM
    (OLD.ticket_id, OLD.version, OLD.currency, OLD.total_amount, OLD.created_at) THEN
    RAISE EXCEPTION 'RO_ESTIMATE_EDIT_THEN_SEND' USING ERRCODE = '23514';
  END IF;
  IF OLD.status = 'SENT' AND NEW.status NOT IN ('SENT','APPROVED','DECLINED','SUPERSEDED') THEN
    RAISE EXCEPTION 'RO_ESTIMATE_BAD_STATE' USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END;$$;
CREATE TRIGGER trg_estimate_frozen BEFORE UPDATE ON estimate
  FOR EACH ROW EXECUTE FUNCTION ro_guard_frozen_estimate();

CREATE OR REPLACE FUNCTION ro_guard_estimate_line()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_status text; v_estimate_id uuid;
BEGIN
  IF TG_OP = 'UPDATE' AND NEW.estimate_id IS DISTINCT FROM OLD.estimate_id THEN
    RAISE EXCEPTION 'RO_LINE_CANNOT_MOVE' USING ERRCODE = '23514';
  END IF;
  IF TG_OP = 'DELETE' THEN
    v_estimate_id := OLD.estimate_id;
  ELSE
    v_estimate_id := NEW.estimate_id;
  END IF;
  SELECT status INTO v_status FROM estimate WHERE id = v_estimate_id FOR UPDATE;
  IF v_status IS NULL OR v_status <> 'DRAFT' THEN
    RAISE EXCEPTION 'RO_ESTIMATE_LINE_FROZEN' USING ERRCODE = '23514';
  END IF;
  IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
  RETURN NEW;
END;$$;
CREATE TRIGGER trg_estimate_line_frozen BEFORE INSERT OR UPDATE OR DELETE ON estimate_line
  FOR EACH ROW EXECUTE FUNCTION ro_guard_estimate_line();

-- Conditional single-row reservation can be performed atomically under READ COMMITTED:
-- UPDATE stock_lot SET qty_reserved = qty_reserved + :qty
-- WHERE id = :lot_id AND qty_on_hand - qty_reserved >= :qty
-- RETURNING id;  -- requires a checked, authorized application transaction.
-- The app MUST first verify verified part+variant compatibility, approved operation,
-- approved estimate and ownership, then create reservation+audit in the SAME transaction.
-- CHECK constraints forbid negative stock and over-reservation even on concurrent calls.

COMMIT;
