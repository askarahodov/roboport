"""Immutable synthetic SKU catalog: metadata, category price history, analogs, logical merge and XLSX staging.

Revision ID: 0124_auto_catalog
Revises: 0123_supplier_booking
Existing stock SKU IDs, FK history and physical ledger are never rewritten.
"""
from alembic import op

revision="0124_auto_catalog"
down_revision="0123_supplier_booking"
branch_labels=None
depends_on=None


def upgrade():
    op.execute("""
      CREATE TABLE auto_catalog_profile(
        sku_id uuid PRIMARY KEY REFERENCES auto_stock_sku(id),
        organization_id uuid NOT NULL REFERENCES auto_organization(id),
        location_id uuid NOT NULL REFERENCES auto_service_location(id),
        category_code text NOT NULL CHECK(category_code ~ '^[A-Z0-9][A-Z0-9_-]{1,31}$'),
        uom text NOT NULL CHECK(uom IN ('PCS','L','KG')),
        barcode text CHECK(barcode IS NULL OR barcode ~ '^[0-9]{8,32}$'),
        brand text CHECK(brand IS NULL OR (length(brand) BETWEEN 1 AND 80 AND brand=btrim(brand))),
        created_by uuid NOT NULL REFERENCES app_user(id),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        UNIQUE(organization_id,location_id,barcode)
      )
    """)
    op.execute("""
      CREATE TABLE auto_catalog_price_batch(
        id uuid PRIMARY KEY,
        request_key uuid NOT NULL UNIQUE,
        organization_id uuid NOT NULL REFERENCES auto_organization(id),
        location_id uuid NOT NULL REFERENCES auto_service_location(id),
        payload_digest text NOT NULL CHECK(payload_digest ~ '^[0-9a-f]{64}$'),
        source text NOT NULL CHECK(source IN ('MANUAL','XLSX','CREATE')),
        created_by uuid NOT NULL REFERENCES app_user(id),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp()
      )
    """)
    op.execute("""
      CREATE TABLE auto_catalog_price_event(
        id uuid PRIMARY KEY,
        batch_id uuid NOT NULL REFERENCES auto_catalog_price_batch(id),
        sku_id uuid NOT NULL REFERENCES auto_stock_sku(id),
        organization_id uuid NOT NULL REFERENCES auto_organization(id),
        location_id uuid NOT NULL REFERENCES auto_service_location(id),
        category_code text NOT NULL,
        version integer NOT NULL CHECK(version BETWEEN 1 AND 1000000000),
        purchase_minor bigint NOT NULL CHECK(purchase_minor BETWEEN 0 AND 1000000000),
        sale_minor bigint NOT NULL CHECK(sale_minor BETWEEN 0 AND 1000000000),
        created_by uuid NOT NULL REFERENCES app_user(id),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        UNIQUE(batch_id,sku_id),UNIQUE(sku_id,version)
      )
    """)
    op.execute("""
      CREATE TABLE auto_catalog_analog(
        id uuid PRIMARY KEY,
        request_key uuid NOT NULL UNIQUE,
        organization_id uuid NOT NULL REFERENCES auto_organization(id),
        location_id uuid NOT NULL REFERENCES auto_service_location(id),
        left_sku_id uuid NOT NULL REFERENCES auto_stock_sku(id),
        right_sku_id uuid NOT NULL REFERENCES auto_stock_sku(id),
        evidence_note text NOT NULL CHECK(length(evidence_note) BETWEEN 3 AND 160),
        created_by uuid NOT NULL REFERENCES app_user(id),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        CHECK(left_sku_id < right_sku_id),
        UNIQUE(organization_id,location_id,left_sku_id,right_sku_id)
      )
    """)
    op.execute("""
      CREATE TABLE auto_catalog_merge(
        id uuid PRIMARY KEY,
        request_key uuid NOT NULL UNIQUE,
        organization_id uuid NOT NULL REFERENCES auto_organization(id),
        location_id uuid NOT NULL REFERENCES auto_service_location(id),
        source_sku_id uuid NOT NULL UNIQUE REFERENCES auto_stock_sku(id),
        target_sku_id uuid NOT NULL REFERENCES auto_stock_sku(id),
        created_by uuid NOT NULL REFERENCES app_user(id),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        CHECK(source_sku_id <> target_sku_id)
      )
    """)
    op.execute("""
      CREATE TABLE auto_catalog_import_preview(
        id uuid PRIMARY KEY,
        request_key uuid NOT NULL UNIQUE,
        organization_id uuid NOT NULL REFERENCES auto_organization(id),
        location_id uuid NOT NULL REFERENCES auto_service_location(id),
        source_sha256 text NOT NULL CHECK(source_sha256 ~ '^[0-9a-f]{64}$'),
        staged_rows jsonb NOT NULL CHECK(jsonb_typeof(staged_rows)='array' AND
          jsonb_array_length(staged_rows) BETWEEN 1 AND 100),
        snapshots jsonb NOT NULL CHECK(jsonb_typeof(snapshots)='array'),
        created_by uuid NOT NULL REFERENCES app_user(id),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        expires_at timestamptz NOT NULL
      )
    """)
    op.execute("""
      CREATE TABLE auto_catalog_import_commit(
        id uuid PRIMARY KEY,
        request_key uuid NOT NULL UNIQUE,
        preview_id uuid NOT NULL UNIQUE REFERENCES auto_catalog_import_preview(id),
        organization_id uuid NOT NULL REFERENCES auto_organization(id),
        location_id uuid NOT NULL REFERENCES auto_service_location(id),
        source_sha256 text NOT NULL CHECK(source_sha256 ~ '^[0-9a-f]{64}$'),
        created_by uuid NOT NULL REFERENCES app_user(id),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp()
      )
    """)
    for table in ("auto_catalog_profile","auto_catalog_price_batch",
                  "auto_catalog_price_event","auto_catalog_analog",
                  "auto_catalog_merge","auto_catalog_import_preview",
                  "auto_catalog_import_commit"):
        op.execute(f"""CREATE TRIGGER {table}_immutable
          BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW
          EXECUTE FUNCTION immutable_auto_stock_event()""")
        op.execute(f"REVOKE ALL ON {table} FROM PUBLIC")
    op.execute("""
      CREATE FUNCTION guard_auto_catalog_identity() RETURNS trigger
      LANGUAGE plpgsql AS $fn$
      DECLARE actor uuid; scope record;
      BEGIN
        actor:=NULLIF(current_setting('roboport.catalog_actor',true),'')::uuid;
        IF actor IS NULL OR actor IS DISTINCT FROM NEW.created_by
        THEN RAISE EXCEPTION 'catalog actor required'; END IF;
        PERFORM 1 FROM auto_service_location l
          JOIN auto_organization o ON o.id=l.organization_id
          WHERE l.id=NEW.location_id AND l.organization_id=NEW.organization_id
            AND l.disabled_at IS NULL AND o.disabled_at IS NULL
          FOR SHARE OF l,o;
        IF NOT FOUND THEN RAISE EXCEPTION 'catalog branch denied'; END IF;
        IF TG_TABLE_NAME='auto_catalog_profile' THEN
          SELECT organization_id,location_id INTO scope FROM auto_stock_sku
            WHERE id=NEW.sku_id FOR SHARE;
          IF NOT FOUND OR (scope.organization_id,scope.location_id) IS DISTINCT
            FROM (NEW.organization_id,NEW.location_id)
          THEN RAISE EXCEPTION 'catalog sku wrong branch'; END IF;
        ELSIF TG_TABLE_NAME='auto_catalog_price_event' THEN
          PERFORM 1 FROM auto_catalog_price_batch b WHERE b.id=NEW.batch_id
            AND b.organization_id=NEW.organization_id AND b.location_id=NEW.location_id
            AND b.created_by=actor FOR SHARE;
          IF NOT FOUND THEN RAISE EXCEPTION 'catalog price batch mismatch'; END IF;
          PERFORM 1 FROM auto_catalog_profile p WHERE p.sku_id=NEW.sku_id
            AND p.organization_id=NEW.organization_id AND p.location_id=NEW.location_id
            AND p.category_code=NEW.category_code FOR SHARE;
          IF NOT FOUND THEN RAISE EXCEPTION 'catalog category mismatch'; END IF;
          PERFORM 1 FROM auto_stock_sku WHERE id=NEW.sku_id FOR UPDATE;
          IF EXISTS(SELECT 1 FROM auto_catalog_merge m WHERE m.source_sku_id=NEW.sku_id)
          THEN RAISE EXCEPTION 'merged sku cannot be repriced'; END IF;
          IF NEW.version<>1+COALESCE((SELECT max(version)
              FROM auto_catalog_price_event WHERE sku_id=NEW.sku_id),0)
          THEN RAISE EXCEPTION 'catalog price version conflict'; END IF;
        ELSIF TG_TABLE_NAME='auto_catalog_import_commit' THEN
          PERFORM 1 FROM auto_catalog_import_preview p WHERE p.id=NEW.preview_id
            AND p.organization_id=NEW.organization_id AND p.location_id=NEW.location_id
            AND p.created_by=actor AND p.source_sha256=NEW.source_sha256
            AND p.expires_at>clock_timestamp() FOR SHARE;
          IF NOT FOUND THEN RAISE EXCEPTION 'stale catalog preview'; END IF;
        END IF;
        RETURN NEW;
      END $fn$
    """)
    for table in ("auto_catalog_profile","auto_catalog_price_batch",
                  "auto_catalog_price_event","auto_catalog_analog",
                  "auto_catalog_merge","auto_catalog_import_preview",
                  "auto_catalog_import_commit"):
        op.execute(f"""CREATE TRIGGER {table}_actor
          BEFORE INSERT ON {table} FOR EACH ROW
          EXECUTE FUNCTION guard_auto_catalog_identity()""")
    op.execute("""
      CREATE FUNCTION guard_auto_catalog_relation() RETURNS trigger
      LANGUAGE plpgsql AS $fn$
      DECLARE a record; b record; available record;
      BEGIN
        IF TG_TABLE_NAME='auto_catalog_analog' THEN
          SELECT organization_id,location_id,category_code,uom INTO a
            FROM auto_catalog_profile WHERE sku_id=NEW.left_sku_id FOR SHARE;
          SELECT organization_id,location_id,category_code,uom INTO b
            FROM auto_catalog_profile WHERE sku_id=NEW.right_sku_id FOR SHARE;
          IF a IS NULL OR b IS NULL OR
            (a.organization_id,a.location_id,a.category_code,a.uom) IS DISTINCT FROM
            (b.organization_id,b.location_id,b.category_code,b.uom)
            OR (a.organization_id,a.location_id) IS DISTINCT FROM
            (NEW.organization_id,NEW.location_id)
            OR EXISTS(SELECT 1 FROM auto_catalog_merge
                      WHERE source_sku_id IN(NEW.left_sku_id,NEW.right_sku_id))
          THEN RAISE EXCEPTION 'analog sku incompatible'; END IF;
        ELSIF TG_TABLE_NAME='auto_catalog_merge' THEN
          PERFORM 1 FROM auto_stock_sku
            WHERE id IN (NEW.source_sku_id,NEW.target_sku_id)
            ORDER BY id FOR UPDATE;
          SELECT organization_id,location_id,category_code,uom,barcode,brand INTO a
            FROM auto_catalog_profile WHERE sku_id=NEW.source_sku_id FOR SHARE;
          SELECT organization_id,location_id,category_code,uom,barcode,brand INTO b
            FROM auto_catalog_profile WHERE sku_id=NEW.target_sku_id FOR SHARE;
          IF a IS NULL OR b IS NULL OR
            (a.organization_id,a.location_id,a.category_code,a.uom) IS DISTINCT FROM
            (b.organization_id,b.location_id,b.category_code,b.uom)
            OR (a.organization_id,a.location_id) IS DISTINCT FROM
            (NEW.organization_id,NEW.location_id)
            OR (a.barcode IS NOT NULL AND b.barcode IS NOT NULL AND a.barcode<>b.barcode)
            OR (a.brand IS NOT NULL AND b.brand IS NOT NULL AND a.brand<>b.brand)
            OR EXISTS(SELECT 1 FROM auto_catalog_merge
                WHERE source_sku_id IN(NEW.source_sku_id,NEW.target_sku_id)
                   OR target_sku_id=NEW.source_sku_id)
          THEN RAISE EXCEPTION 'unsafe sku merge'; END IF;
          SELECT on_hand,reserved INTO available FROM auto_stock_balance
            WHERE sku_id=NEW.source_sku_id;
          IF available.on_hand<>0 OR available.reserved<>0
             OR EXISTS(
               SELECT 1 FROM auto_work_order_stock_event wo
               WHERE wo.sku_id=NEW.source_sku_id
               GROUP BY wo.work_order_id
               HAVING SUM(CASE WHEN wo.action='ISSUE' THEN wo.quantity
                               WHEN wo.action='RETURN' THEN -wo.quantity
                               ELSE 0 END) > 0
             )
             OR EXISTS(SELECT 1 FROM auto_stock_lot src
                 JOIN auto_stock_lot dst ON dst.sku_id=NEW.target_sku_id
                 WHERE src.sku_id=NEW.source_sku_id AND
                   (src.batch_code,src.source_revision) IS DISTINCT FROM
                   (dst.batch_code,dst.source_revision))
             OR EXISTS(SELECT 1 FROM auto_stock_lot_balance lb
               JOIN auto_stock_lot l ON l.id=lb.lot_id
               WHERE l.sku_id=NEW.source_sku_id AND lb.allocated<>0)
             OR EXISTS(SELECT 1 FROM auto_stock_serial
                        WHERE sku_id=NEW.source_sku_id)
          THEN RAISE EXCEPTION 'sku has stock or serial history'; END IF;
        END IF;
        RETURN NEW;
      END $fn$
    """)
    for table in ("auto_catalog_analog","auto_catalog_merge"):
        op.execute(f"""CREATE TRIGGER {table}_relation
          BEFORE INSERT ON {table} FOR EACH ROW
          EXECUTE FUNCTION guard_auto_catalog_relation()""")
    op.execute("""CREATE INDEX ix_auto_catalog_price_sku ON
      auto_catalog_price_event(sku_id,version DESC)""")
    op.execute("""CREATE INDEX ix_auto_catalog_preview_expiry ON
      auto_catalog_import_preview(organization_id,location_id,expires_at)""")

    # A logical merge is safe only if the source can NEVER receive new
    # physical events or lots afterward. This guard acquires the same SKU
    # row lock as the merge, serializing the race on either side of commit.
    op.execute("""
      CREATE FUNCTION guard_auto_catalog_merged_stock() RETURNS trigger
      LANGUAGE plpgsql AS $fn$
      BEGIN
        PERFORM 1 FROM auto_stock_sku WHERE id=NEW.sku_id FOR UPDATE;
        IF EXISTS(SELECT 1 FROM auto_catalog_merge
                  WHERE source_sku_id=NEW.sku_id)
        THEN RAISE EXCEPTION 'merged catalog SKU cannot receive new stock'; END IF;
        RETURN NEW;
      END $fn$
    """)
    op.execute("""CREATE TRIGGER aaa_catalog_merged_stock_event
      BEFORE INSERT ON auto_stock_event FOR EACH ROW
      EXECUTE FUNCTION guard_auto_catalog_merged_stock()""")
    op.execute("""CREATE TRIGGER aaa_catalog_merged_lot
      BEFORE INSERT ON auto_stock_lot FOR EACH ROW
      EXECUTE FUNCTION guard_auto_catalog_merged_stock()""")



def downgrade():
    # Disposable synthetic DB only: never erase imported catalog or history in production.
    op.execute("DROP TRIGGER aaa_catalog_merged_stock_event ON auto_stock_event")
    op.execute("DROP TRIGGER aaa_catalog_merged_lot ON auto_stock_lot")
    op.execute("DROP FUNCTION guard_auto_catalog_merged_stock()")
    for table in ("auto_catalog_import_commit","auto_catalog_import_preview",
                  "auto_catalog_merge","auto_catalog_analog",
                  "auto_catalog_price_event","auto_catalog_price_batch",
                  "auto_catalog_profile"):
        op.execute(f"DROP TABLE {table}")
    op.execute("DROP FUNCTION guard_auto_catalog_relation()")
    op.execute("DROP FUNCTION guard_auto_catalog_identity()")
