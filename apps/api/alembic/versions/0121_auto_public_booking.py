"""Public dev-only appointment request: no VIN, PII or provider delivery.

Revision ID: 0121_auto_public_booking
Revises: 0120_auto_treasury
"""
from alembic import op
from sqlalchemy import text

revision="0121_auto_public_booking"
down_revision="0120_auto_treasury"
branch_labels=None
depends_on=None


def upgrade():
    op.execute("""
      CREATE TABLE auto_public_booking_offer (
        id uuid PRIMARY KEY,
        organization_id uuid NOT NULL REFERENCES auto_organization(id),
        location_id uuid NOT NULL REFERENCES auto_service_location(id),
        bay_id uuid NOT NULL REFERENCES auto_work_bay(id),
        service_id uuid NOT NULL REFERENCES auto_service_catalog(id),
        actor_id uuid NOT NULL REFERENCES app_user(id),
        request_key uuid NOT NULL UNIQUE,
        policy_version text NOT NULL CHECK(policy_version ~ '^DEV-CONSENT-[0-9]{4}$'),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp()
      )
    """)
    op.execute("""
      CREATE TABLE auto_public_booking_material (
        offer_id uuid NOT NULL REFERENCES auto_public_booking_offer(id),
        sku_id uuid NOT NULL REFERENCES auto_stock_sku(id),
        item_kind text NOT NULL CHECK(item_kind IN ('PART','CONSUMABLE')),
        quantity bigint NOT NULL CHECK(quantity BETWEEN 1 AND 1000000000),
        actor_id uuid NOT NULL REFERENCES app_user(id),
        PRIMARY KEY (offer_id,sku_id)
      )
    """)
    op.execute("""
      CREATE TABLE auto_public_booking_offer_revoke (
        offer_id uuid PRIMARY KEY REFERENCES auto_public_booking_offer(id),
        actor_id uuid NOT NULL REFERENCES app_user(id),
        request_key uuid NOT NULL UNIQUE,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp()
      )
    """)
    op.execute("""
      CREATE TABLE auto_public_booking_challenge (
        id uuid PRIMARY KEY,
        offer_id uuid NOT NULL REFERENCES auto_public_booking_offer(id),
        answer_digest text NOT NULL CHECK(answer_digest ~ '^[0-9a-f]{64}$'),
        expires_at timestamptz NOT NULL,
        used_at timestamptz,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        CHECK(expires_at>created_at),
        CHECK(used_at IS NULL OR used_at>=created_at)
      )
    """)
    op.execute("""CREATE INDEX auto_public_challenge_recent
      ON auto_public_booking_challenge (offer_id,created_at DESC)""")
    op.execute("""
      CREATE TABLE auto_public_booking_request (
        id uuid PRIMARY KEY,
        offer_id uuid NOT NULL REFERENCES auto_public_booking_offer(id),
        request_key uuid NOT NULL UNIQUE,
        challenge_id uuid NOT NULL UNIQUE REFERENCES auto_public_booking_challenge(id),
        starts_at timestamptz NOT NULL,
        ends_at timestamptz NOT NULL,
        processing_consent boolean NOT NULL CHECK(processing_consent),
        marketing_consent boolean NOT NULL,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        CHECK(ends_at>starts_at AND ends_at<=starts_at+interval '24 hours')
      )
    """)
    op.execute("""CREATE INDEX auto_public_requests_offer
      ON auto_public_booking_request (offer_id,created_at,id)""")
    op.execute("""
      CREATE TABLE auto_public_booking_claim (
        request_id uuid PRIMARY KEY REFERENCES auto_public_booking_request(id),
        actor_id uuid NOT NULL REFERENCES app_user(id),
        customer_link_id uuid NOT NULL REFERENCES auto_vehicle_customer_link(id),
        request_key uuid NOT NULL UNIQUE,
        processing_consent boolean NOT NULL CHECK(processing_consent),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp()
      )
    """)
    op.execute("""
      CREATE TABLE auto_public_booking_consent_withdrawal (
        request_id uuid PRIMARY KEY REFERENCES auto_public_booking_claim(request_id),
        actor_id uuid NOT NULL REFERENCES app_user(id),
        request_key uuid NOT NULL UNIQUE,
        channel_code text NOT NULL DEFAULT 'MARKETING'
          CHECK(channel_code='MARKETING'),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp()
      )
    """)
    op.execute("""
      CREATE TABLE auto_public_booking_fulfillment (
        request_id uuid PRIMARY KEY REFERENCES auto_public_booking_claim(request_id),
        appointment_id uuid NOT NULL UNIQUE REFERENCES auto_appointment(id),
        work_order_id uuid NOT NULL UNIQUE REFERENCES auto_work_order(id),
        crm_lead_id uuid NOT NULL REFERENCES auto_crm_lead(id),
        actor_id uuid NOT NULL REFERENCES app_user(id),
        request_key uuid NOT NULL UNIQUE,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp()
      )
    """)
    op.execute("""
      CREATE FUNCTION guard_auto_public_offer()
      RETURNS trigger LANGUAGE plpgsql AS $f$
      BEGIN
        IF NOT EXISTS (
          SELECT 1 FROM auto_organization o
          JOIN auto_service_location l ON l.organization_id=o.id
             AND l.id=NEW.location_id AND l.disabled_at IS NULL
          JOIN auto_work_bay b ON b.id=NEW.bay_id
             AND b.location_id=l.id AND b.organization_id=o.id
             AND b.disabled_at IS NULL
          JOIN auto_service_catalog service ON service.id=NEW.service_id
             AND service.location_id=l.id AND service.organization_id=o.id
          JOIN auto_organization_role_grant g ON g.organization_id=o.id
             AND g.user_id=NEW.actor_id AND g.role_code='MANAGER'
             AND g.revoked_at IS NULL
             AND (g.location_id IS NULL OR g.location_id=l.id)
          JOIN app_user u ON u.id=NEW.actor_id AND u.status='ACTIVE'
          WHERE o.id=NEW.organization_id AND o.disabled_at IS NULL
          FOR SHARE OF o,l,b,service,g,u
        ) THEN RAISE EXCEPTION 'published public offer needs manager and scoped resource'; END IF;
        RETURN NEW;
      END $f$
    """)
    op.execute("""CREATE TRIGGER auto_public_offer_guard BEFORE INSERT
       ON auto_public_booking_offer FOR EACH ROW EXECUTE FUNCTION guard_auto_public_offer()""")
    op.execute("""
      CREATE FUNCTION guard_auto_public_offer_revoke()
      RETURNS trigger LANGUAGE plpgsql AS $f$
      BEGIN
        IF NOT EXISTS (
          SELECT 1 FROM auto_public_booking_offer offer
          JOIN auto_organization org ON org.id=offer.organization_id
            AND org.disabled_at IS NULL
          JOIN auto_organization_role_grant grant
            ON grant.organization_id=offer.organization_id
            AND grant.user_id=NEW.actor_id AND grant.role_code='MANAGER'
            AND grant.revoked_at IS NULL
            AND (grant.location_id IS NULL OR grant.location_id=offer.location_id)
          JOIN app_user actor ON actor.id=NEW.actor_id AND actor.status='ACTIVE'
          WHERE offer.id=NEW.offer_id
          FOR SHARE OF offer,org,grant,actor
        ) THEN RAISE EXCEPTION 'only live scoped MANAGER can revoke offer'; END IF;
        RETURN NEW;
      END $f$
    """)
    op.execute("""CREATE TRIGGER auto_public_offer_revoke_guard BEFORE INSERT
       ON auto_public_booking_offer_revoke FOR EACH ROW
       EXECUTE FUNCTION guard_auto_public_offer_revoke()""")
    op.execute("""
      CREATE FUNCTION guard_auto_public_material()
      RETURNS trigger LANGUAGE plpgsql AS $f$
      BEGIN
        IF NOT EXISTS (
          SELECT 1 FROM auto_public_booking_offer o
          JOIN auto_stock_sku s ON s.id=NEW.sku_id
            AND s.organization_id=o.organization_id AND s.location_id=o.location_id
          JOIN auto_organization_role_grant g
            ON g.organization_id=o.organization_id AND g.user_id=NEW.actor_id
            AND g.role_code='WAREHOUSE' AND g.revoked_at IS NULL
            AND (g.location_id IS NULL OR g.location_id=o.location_id)
          WHERE o.id=NEW.offer_id FOR SHARE OF o,s,g
        ) THEN RAISE EXCEPTION 'material needs scoped WAREHOUSE approval'; END IF;
        RETURN NEW;
      END $f$
    """)
    op.execute("""CREATE TRIGGER auto_public_material_guard BEFORE INSERT
       ON auto_public_booking_material FOR EACH ROW EXECUTE FUNCTION guard_auto_public_material()""")
    op.execute("""
      CREATE FUNCTION guard_auto_public_claim()
      RETURNS trigger LANGUAGE plpgsql AS $f$
      DECLARE tenant uuid; branch uuid;
      BEGIN
        SELECT offer.organization_id,offer.location_id INTO tenant,branch
          FROM auto_public_booking_request request
          JOIN auto_public_booking_offer offer ON offer.id=request.offer_id
          WHERE request.id=NEW.request_id;
        IF tenant IS NULL OR NOT EXISTS (
          SELECT 1 FROM auto_vehicle_customer_link proof
          JOIN auto_vehicle_location_binding binding
            ON binding.vehicle_id=proof.vehicle_id AND binding.location_id=branch
          JOIN account_customer_link acl
            ON acl.customer_id=proof.customer_id AND acl.user_id=NEW.actor_id
            AND acl.revoked_at IS NULL
          JOIN user_role role ON role.user_id=NEW.actor_id
            AND role.role_code='CLIENT' AND role.revoked_at IS NULL
            AND (role.expires_at IS NULL OR role.expires_at>now())
          JOIN app_user u ON u.id=NEW.actor_id AND u.status='ACTIVE'
          WHERE proof.id=NEW.customer_link_id AND proof.verified_at IS NOT NULL
            AND proof.verified_by IS NOT NULL AND proof.revoked_at IS NULL
          FOR SHARE OF proof,binding,acl,role,u
        ) THEN RAISE EXCEPTION 'public claim needs original verified client'; END IF;
        RETURN NEW;
      END $f$
    """)
    op.execute("""CREATE TRIGGER auto_public_claim_guard BEFORE INSERT
       ON auto_public_booking_claim FOR EACH ROW EXECUTE FUNCTION guard_auto_public_claim()""")
    op.execute("""
      CREATE FUNCTION guard_auto_public_fulfillment()
      RETURNS trigger LANGUAGE plpgsql AS $f$
      DECLARE claim_row record;
      BEGIN
        SELECT req.offer_id,offer.organization_id,offer.location_id,offer.service_id,
          claim.customer_link_id,req.starts_at,req.ends_at
          INTO claim_row
        FROM auto_public_booking_claim claim
        JOIN auto_public_booking_request req ON req.id=claim.request_id
        JOIN auto_public_booking_offer offer ON offer.id=req.offer_id
        WHERE claim.request_id=NEW.request_id;
        IF NOT FOUND OR
          NOT EXISTS(SELECT 1 FROM auto_work_order w
            JOIN auto_appointment a ON a.id=w.appointment_id
            JOIN auto_bay_booking b ON b.id=a.booking_id
            WHERE w.id=NEW.work_order_id AND a.id=NEW.appointment_id
              AND w.organization_id=claim_row.organization_id
              AND w.location_id=claim_row.location_id
              AND a.customer_link_id=claim_row.customer_link_id
              AND b.starts_at=claim_row.starts_at AND b.ends_at=claim_row.ends_at
              AND b.canceled_at IS NULL
            FOR SHARE OF w,a,b)
          OR NOT EXISTS(SELECT 1 FROM auto_work_order_service service
               WHERE service.work_order_id=NEW.work_order_id
                 AND service.service_id=claim_row.service_id
                 AND service.selection_origin='BOOKING')
          OR EXISTS (SELECT 1 FROM auto_public_booking_material material
              WHERE material.offer_id=claim_row.offer_id AND NOT EXISTS (
                SELECT 1 FROM auto_appointment_material_selection selected
                WHERE selected.work_order_id=NEW.work_order_id
                  AND selected.sku_id=material.sku_id
                  AND selected.item_kind=material.item_kind
                  AND selected.quantity=material.quantity))
          OR NOT EXISTS (SELECT 1 FROM auto_crm_lead lead
              WHERE lead.id=NEW.crm_lead_id
                AND lead.organization_id=claim_row.organization_id
                AND lead.location_id=claim_row.location_id
                AND lead.customer_link_id=claim_row.customer_link_id)
        THEN RAISE EXCEPTION 'public fulfillment lineage mismatched'; END IF;
        RETURN NEW;
      END $f$
    """)
    op.execute("""CREATE TRIGGER auto_public_fulfillment_guard BEFORE INSERT
       ON auto_public_booking_fulfillment FOR EACH ROW
       EXECUTE FUNCTION guard_auto_public_fulfillment()""")
    op.execute("""
      CREATE FUNCTION guard_auto_public_withdraw()
      RETURNS trigger LANGUAGE plpgsql AS $f$
      BEGIN
        IF NOT EXISTS (SELECT 1 FROM auto_public_booking_claim c
           WHERE c.request_id=NEW.request_id AND c.actor_id=NEW.actor_id
           FOR SHARE)
        THEN RAISE EXCEPTION 'only verified claimant can withdraw marketing'; END IF;
        RETURN NEW;
      END $f$
    """)
    op.execute("""CREATE TRIGGER auto_public_withdraw_guard BEFORE INSERT
       ON auto_public_booking_consent_withdrawal FOR EACH ROW
       EXECUTE FUNCTION guard_auto_public_withdraw()""")
    for table in (
        "auto_public_booking_offer","auto_public_booking_material",
        "auto_public_booking_offer_revoke","auto_public_booking_request",
        "auto_public_booking_claim","auto_public_booking_consent_withdrawal",
        "auto_public_booking_fulfillment"
    ):
        op.execute(f"""CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE
          ON {table} FOR EACH ROW EXECUTE FUNCTION immutable_auto_stock_event()""")
        op.execute(f"REVOKE ALL ON {table} FROM PUBLIC")
    op.execute("REVOKE ALL ON auto_public_booking_challenge FROM PUBLIC")


def downgrade():
    # Never delete audit on production; disposable PG17 only.
    op.execute("DROP TABLE auto_public_booking_fulfillment")
    op.execute("DROP FUNCTION guard_auto_public_fulfillment()")
    op.execute("DROP TABLE auto_public_booking_consent_withdrawal")
    op.execute("DROP FUNCTION guard_auto_public_withdraw()")
    op.execute("DROP TABLE auto_public_booking_claim")
    op.execute("DROP FUNCTION guard_auto_public_claim()")
    op.execute("DROP TABLE auto_public_booking_request")
    op.execute("DROP TABLE auto_public_booking_challenge")
    op.execute("DROP TABLE auto_public_booking_offer_revoke")
    op.execute("DROP FUNCTION guard_auto_public_offer_revoke()")
    op.execute("DROP TABLE auto_public_booking_material")
    op.execute("DROP FUNCTION guard_auto_public_material()")
    op.execute("DROP TABLE auto_public_booking_offer")
    op.execute("DROP FUNCTION guard_auto_public_offer()")
