"""Scoped configurable location opening hours, closed dates and audit.

Revision ID: 0093_auto_location_opening_hours
Revises: 0092_auto_booking_materials

No default opening hours: absent policy preserves legacy dev fixtures, but
unconfigured branches are NOT eligible for production release.
"""
from alembic import op
from sqlalchemy import text

revision = "0093_auto_location_opening_hours"
down_revision = "0092_auto_booking_materials"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE TABLE auto_location_opening_policy (
        location_id uuid PRIMARY KEY REFERENCES auto_service_location(id),
        organization_id uuid NOT NULL REFERENCES auto_organization(id),
        windows jsonb NOT NULL,
        closed_dates date[] NOT NULL DEFAULT '{}',
        version bigint NOT NULL DEFAULT 1 CHECK (version >= 1),
        changed_by uuid NOT NULL REFERENCES app_user(id),
        changed_at timestamptz NOT NULL DEFAULT now(),
        CHECK (jsonb_typeof(windows)='array')
      )
    """)
    op.execute("""
      CREATE TABLE auto_location_opening_history (
        location_id uuid NOT NULL REFERENCES auto_service_location(id),
        organization_id uuid NOT NULL REFERENCES auto_organization(id),
        version bigint NOT NULL,
        windows jsonb NOT NULL,
        closed_dates date[] NOT NULL,
        changed_by uuid NOT NULL REFERENCES app_user(id),
        changed_at timestamptz NOT NULL,
        PRIMARY KEY(location_id,version)
      )
    """)
    op.execute("""
      CREATE FUNCTION auto_location_hours_allow(
        p_zone text, p_windows jsonb, p_closed date[],
        p_starts timestamptz, p_ends timestamptz
      ) RETURNS boolean LANGUAGE plpgsql STABLE AS $fn$
      DECLARE
        local_start timestamp;
        local_end timestamp;
        start_date date;
        start_min numeric;
        end_min numeric;
        weekday int;
        opening_entry jsonb;
      BEGIN
        IF p_zone IS NULL OR p_windows IS NULL OR p_starts IS NULL
           OR p_ends IS NULL OR p_ends <= p_starts THEN
          RETURN false;
        END IF;
        local_start := p_starts AT TIME ZONE p_zone;
        local_end := p_ends AT TIME ZONE p_zone;
        start_date := local_start::date;
        IF start_date = ANY(p_closed) THEN
          RETURN false;
        END IF;
        IF local_end::date = start_date THEN
          end_min := extract(hour from local_end)*60
                   + extract(minute from local_end)
                   + extract(second from local_end)/60;
        ELSIF local_end::date = start_date + 1
              AND local_end::time = time '00:00:00' THEN
          end_min := 1440;
        ELSE
          RETURN false;
        END IF;
        start_min := extract(hour from local_start)*60
                   + extract(minute from local_start)
                   + extract(second from local_start)/60;
        weekday := extract(isodow from local_start)::int;
        FOR opening_entry IN SELECT value FROM jsonb_array_elements(p_windows) LOOP
          IF (opening_entry->>'weekday')::int=weekday
             AND start_min >= (opening_entry->>'opensMinute')::int
             AND end_min <= (opening_entry->>'closesMinute')::int THEN
            RETURN true;
          END IF;
        END LOOP;
        RETURN false;
      END $fn$
    """)
    op.execute("""
      CREATE FUNCTION guard_auto_location_opening_policy()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      DECLARE
        item jsonb;
        day_no int;
        open_min int;
        close_min int;
        duplicates int;
      BEGIN
        IF TG_OP='DELETE' THEN
          RAISE EXCEPTION 'opening policy deletion forbidden';
        END IF;
        -- Serialize policy updates and booking INSERTs on the location.
        PERFORM 1 FROM auto_service_location l
          JOIN auto_organization o ON o.id=l.organization_id
          WHERE l.id=NEW.location_id AND l.organization_id=NEW.organization_id
            AND l.disabled_at IS NULL AND o.disabled_at IS NULL
          FOR UPDATE OF l;
        IF NOT FOUND THEN
          RAISE EXCEPTION 'opening policy location unavailable';
        END IF;
        IF TG_OP='UPDATE' THEN
          IF (NEW.location_id,NEW.organization_id)
              IS DISTINCT FROM (OLD.location_id,OLD.organization_id)
             OR NEW.version<>OLD.version+1
             OR NEW.changed_at<OLD.changed_at THEN
            RAISE EXCEPTION 'opening policy scope/version immutable';
          END IF;
        ELSIF NEW.version<>1 THEN
          RAISE EXCEPTION 'first opening policy revision must be one';
        END IF;
        IF jsonb_typeof(NEW.windows) IS DISTINCT FROM 'array'
            OR jsonb_array_length(NEW.windows)>28
            OR coalesce(array_length(NEW.closed_dates,1),0)>120 THEN
          RAISE EXCEPTION 'invalid opening policy limits';
        END IF;
        SELECT count(*)-count(DISTINCT day) INTO duplicates
          FROM unnest(NEW.closed_dates) AS dates(day);
        IF duplicates<>0 OR array_position(NEW.closed_dates,NULL) IS NOT NULL THEN
          RAISE EXCEPTION 'duplicate or null closed date';
        END IF;
        FOR item IN SELECT value FROM jsonb_array_elements(NEW.windows) LOOP
          IF jsonb_typeof(item) IS DISTINCT FROM 'object'
             OR (SELECT count(*) FROM jsonb_object_keys(item))<>3
             OR NOT (item ? 'weekday' AND item ? 'opensMinute'
                     AND item ? 'closesMinute')
             OR jsonb_typeof(item->'weekday') IS DISTINCT FROM 'number'
             OR jsonb_typeof(item->'opensMinute') IS DISTINCT FROM 'number'
             OR jsonb_typeof(item->'closesMinute') IS DISTINCT FROM 'number'
             OR (item->>'weekday') !~ '^[0-9]+$'
             OR (item->>'opensMinute') !~ '^[0-9]+$'
             OR (item->>'closesMinute') !~ '^[0-9]+$' THEN
            RAISE EXCEPTION 'invalid opening window structure';
          END IF;
          day_no := (item->>'weekday')::int;
          open_min := (item->>'opensMinute')::int;
          close_min := (item->>'closesMinute')::int;
          IF day_no NOT BETWEEN 1 AND 7
             OR open_min NOT BETWEEN 0 AND 1439
             OR close_min NOT BETWEEN 1 AND 1440
             OR open_min >= close_min THEN
            RAISE EXCEPTION 'invalid opening window bounds';
          END IF;
        END LOOP;
        IF EXISTS (
          SELECT 1
          FROM jsonb_array_elements(NEW.windows) WITH ORDINALITY a(entry,n)
          JOIN jsonb_array_elements(NEW.windows) WITH ORDINALITY b(entry,n)
            ON a.n < b.n
           AND a.entry->>'weekday'=b.entry->>'weekday'
          WHERE (a.entry->>'opensMinute')::int
                  < (b.entry->>'closesMinute')::int
            AND (b.entry->>'opensMinute')::int
                  < (a.entry->>'closesMinute')::int
        ) THEN
          RAISE EXCEPTION 'opening windows overlap';
        END IF;
        -- Policy cannot strand an existing future booking (including
        -- rescheduled current bookings). The update is all-or-nothing.
        IF EXISTS (
          SELECT 1 FROM auto_bay_booking b
          JOIN auto_service_location l ON l.id=b.location_id
          WHERE b.location_id=NEW.location_id
            AND b.organization_id=NEW.organization_id
            AND b.canceled_at IS NULL AND b.ends_at>clock_timestamp()
            AND NOT auto_location_hours_allow(
              l.timezone_name,NEW.windows,NEW.closed_dates,
              b.starts_at,b.ends_at
            )
        ) THEN
          RAISE EXCEPTION 'opening policy conflicts with active bookings';
        END IF;
        RETURN NEW;
      END $fn$
    """)
    op.execute("""
      CREATE TRIGGER auto_location_opening_policy_guard
      BEFORE INSERT OR UPDATE OR DELETE ON auto_location_opening_policy
      FOR EACH ROW EXECUTE FUNCTION guard_auto_location_opening_policy()
    """)
    op.execute("""
      CREATE FUNCTION record_auto_location_opening_history()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      BEGIN
        INSERT INTO auto_location_opening_history
          (location_id,organization_id,version,windows,closed_dates,
           changed_by,changed_at)
        VALUES(NEW.location_id,NEW.organization_id,NEW.version,
               NEW.windows,NEW.closed_dates,NEW.changed_by,NEW.changed_at);
        RETURN NEW;
      END $fn$
    """)
    op.execute("""
      CREATE TRIGGER auto_location_opening_history_append
      AFTER INSERT OR UPDATE ON auto_location_opening_policy
      FOR EACH ROW EXECUTE FUNCTION record_auto_location_opening_history()
    """)
    op.execute("""
      CREATE FUNCTION deny_auto_location_opening_history_mutation()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      BEGIN
        RAISE EXCEPTION 'opening history immutable';
      END $fn$
    """)
    op.execute("""
      CREATE TRIGGER auto_location_opening_history_immutable
      BEFORE UPDATE OR DELETE ON auto_location_opening_history
      FOR EACH ROW EXECUTE FUNCTION deny_auto_location_opening_history_mutation()
    """)
    op.execute("""
      CREATE FUNCTION guard_auto_bay_opening_hours()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      DECLARE
        zone text;
        policy record;
      BEGIN
        -- Row lock shared with the MANAGER update to close check/change races.
        SELECT timezone_name INTO zone
        FROM auto_service_location WHERE id=NEW.location_id
          AND organization_id=NEW.organization_id FOR SHARE;
        IF NOT FOUND THEN
          RAISE EXCEPTION 'location unavailable for booking';
        END IF;
        SELECT windows,closed_dates INTO policy
        FROM auto_location_opening_policy
        WHERE location_id=NEW.location_id AND organization_id=NEW.organization_id
        FOR SHARE;
        IF FOUND AND NOT auto_location_hours_allow(
             zone,policy.windows,policy.closed_dates,
             NEW.starts_at,NEW.ends_at) THEN
          RAISE EXCEPTION 'appointment outside opening hours';
        END IF;
        RETURN NEW;
      END $fn$
    """)
    op.execute("""
      CREATE TRIGGER auto_bay_opening_hours_guard
      BEFORE INSERT ON auto_bay_booking
      FOR EACH ROW EXECUTE FUNCTION guard_auto_bay_opening_hours()
    """)


def downgrade():
    conn = op.get_bind()
    if conn.execute(text(
        "SELECT 1 FROM auto_location_opening_history LIMIT 1"
    )).first():
        raise RuntimeError("opening schedule history exists: use audited restore")
    op.execute("DROP TRIGGER auto_bay_opening_hours_guard ON auto_bay_booking")
    op.execute("DROP FUNCTION guard_auto_bay_opening_hours()")
    op.execute("DROP TRIGGER auto_location_opening_history_immutable ON auto_location_opening_history")
    op.execute("DROP FUNCTION deny_auto_location_opening_history_mutation()")
    op.execute("DROP TRIGGER auto_location_opening_history_append ON auto_location_opening_policy")
    op.execute("DROP FUNCTION record_auto_location_opening_history()")
    op.execute("DROP TRIGGER auto_location_opening_policy_guard ON auto_location_opening_policy")
    op.execute("DROP FUNCTION guard_auto_location_opening_policy()")
    op.execute("DROP FUNCTION auto_location_hours_allow(text,jsonb,date[],timestamptz,timestamptz)")
    op.execute("DROP TABLE auto_location_opening_history")
    op.execute("DROP TABLE auto_location_opening_policy")
