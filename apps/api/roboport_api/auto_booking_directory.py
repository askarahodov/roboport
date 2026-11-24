"""Поиск ресурсов записи и состав неизменяемого комплекса без раскрытия цен."""
from .auto_org_authority import organization_role_allows


class BookingDirectoryDenied(ValueError):
    pass


def _require(conn,token,org,loc,role):
    if not organization_role_allows(conn,token,org,role,loc,for_mutation=True):
        raise BookingDirectoryDenied("FORBIDDEN")


def complex_composition(conn,token,org,loc,ident):
    """Проверяем существующий коммерческий scope и разрешение на резерв."""
    from .auto_work_complexes import _priced_scope, ComplexDenied
    _require(conn,token,org,loc,"INTAKE")
    _require(conn,token,org,loc,"MANAGER")
    try:
        _priced_scope(conn,token,org,loc,"CREATE")
    except ComplexDenied as exc:
        raise BookingDirectoryDenied("FORBIDDEN") from exc
    if not conn.execute("""
        SELECT 1 FROM auto_work_complex c JOIN auto_work_complex_activation a ON a.complex_id=c.id
        WHERE c.id=%s AND c.organization_id=%s AND c.location_id=%s
    """,(ident,org,loc)).fetchone():
        raise BookingDirectoryDenied("FORBIDDEN")
    minutes=conn.execute("""
        SELECT COALESCE(SUM(s.duration_minutes),0) FROM auto_work_complex_service c
        JOIN auto_service_catalog s ON s.id=c.service_id
          AND s.organization_id=%s AND s.location_id=%s WHERE c.complex_id=%s
    """,(org,loc,ident)).fetchone()[0]
    materials=conn.execute("""
        SELECT m.sku_id,m.quantity FROM auto_work_complex_material m
        WHERE m.complex_id=%s ORDER BY m.sku_id
    """,(ident,)).fetchall()
    _require(conn,token,org,loc,"WAREHOUSE")
    if not 5<=minutes<=1440:
        raise BookingDirectoryDenied("COMPLEX_DURATION_UNAVAILABLE")
    return minutes,materials


# Каждая выборка ограничена одним филиалом и возвращает только поля,
# необходимые для записи; даже MANAGER не получает здесь коммерческие цены.
_QUERIES={
    "bays": """SELECT b.id,b.code AS label,NULL::integer AS minutes,NULL::bigint AS available
        FROM auto_work_bay b WHERE b.organization_id=%s AND b.location_id=%s AND b.disabled_at IS NULL""",
    "vehicles": """SELECT proof.id,concat_ws(' ',v.make,v.model,v.model_year::text,
        CASE WHEN v.vin IS NULL THEN NULL ELSE 'VIN ••••'||right(v.vin,4) END) AS label,
        NULL::integer AS minutes,NULL::bigint AS available
        FROM auto_vehicle_customer_link proof JOIN auto_vehicle v ON v.id=proof.vehicle_id
        JOIN auto_vehicle_location_binding b ON b.vehicle_id=v.id
        JOIN auto_service_location l ON l.id=b.location_id
        WHERE l.organization_id=%s AND l.id=%s AND proof.verified_at IS NOT NULL
          AND proof.verified_by IS NOT NULL AND proof.revoked_at IS NULL""",
    "employees": """SELECT DISTINCT s.user_id AS id,s.user_id::text AS label,
        NULL::integer AS minutes,NULL::bigint AS available
        FROM auto_staff_shift s JOIN app_user u ON u.id=s.user_id AND u.status='ACTIVE'
        WHERE s.organization_id=%s AND s.location_id=%s AND s.canceled_at IS NULL
          AND s.ends_at>clock_timestamp()""",
    "services": """SELECT s.id,s.code||' · '||s.title AS label,s.duration_minutes AS minutes,
        NULL::bigint AS available FROM auto_service_catalog s
        WHERE s.organization_id=%s AND s.location_id=%s""",
    "stock": """SELECT s.id,s.article AS label,NULL::integer AS minutes,
        greatest(0,b.on_hand-b.reserved) AS available FROM auto_stock_sku s
        JOIN auto_stock_balance b ON b.sku_id=s.id AND b.organization_id=s.organization_id
          AND b.location_id=s.location_id WHERE s.organization_id=%s AND s.location_id=%s""",
    "complexes": """SELECT c.id,c.code||' · '||c.title AS label,
        (SELECT sum(s.duration_minutes)::integer FROM auto_work_complex_service cs
          JOIN auto_service_catalog s ON s.id=cs.service_id WHERE cs.complex_id=c.id) AS minutes,
        NULL::bigint AS available FROM auto_work_complex c
        JOIN auto_work_complex_activation a ON a.complex_id=c.id
        WHERE c.organization_id=%s AND c.location_id=%s""",
}


def directory(conn,token,org,loc,kind,search,after,*,customer_link=None):
    if kind not in _QUERIES or type(search) is not str or len(search)>64 or "\x00" in search:
        raise ValueError("INVALID_INPUT")
    with conn.transaction():
        if customer_link is not None:
            from .auto_appointment_service import _customer_scope,AppointmentDenied
            if kind!="services":
                raise BookingDirectoryDenied("FORBIDDEN")
            try:
                _customer_scope(conn,token,org,loc,customer_link)
            except AppointmentDenied as exc:
                raise BookingDirectoryDenied("FORBIDDEN") from exc
        else:
            _require(conn,token,org,loc,"INTAKE")
        if kind=="stock":
            _require(conn,token,org,loc,"WAREHOUSE")
        if kind=="complexes":
            from .auto_work_complexes import _priced_scope, ComplexDenied
            _require(conn,token,org,loc,"MANAGER")
            _require(conn,token,org,loc,"WAREHOUSE")
            try:
                _priced_scope(conn,token,org,loc,"CREATE")
            except ComplexDenied as exc:
                raise BookingDirectoryDenied("FORBIDDEN") from exc
        # Идентификатор продолжения не является правом доступа. Поиск буквальный,
        # не SQL wildcard; все параметры проверяются, SQL-фрагменты фиксированы.
        rows=conn.execute("SELECT id,label,minutes,available FROM ("+_QUERIES[kind]+"""
          ) d WHERE (%s::uuid IS NULL OR id>%s::uuid)
            AND (strpos(lower(label),lower(%s))>0 OR strpos(id::text,lower(%s))>0 OR %s='')
            AND (minutes IS NULL OR minutes BETWEEN 5 AND 1440)
          ORDER BY id LIMIT 51
        """,(org,loc,after,after,search,search,search)).fetchall()
        page=rows[:50]
        return {"syntheticOnly":True,"items":[{"id":str(i),"label":label,
            "durationMinutes":minutes,"available":available} for i,label,minutes,available in page],
            "nextAfterId":str(page[-1][0]) if len(rows)>50 else None}
