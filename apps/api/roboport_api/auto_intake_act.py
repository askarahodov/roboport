"""Versioned synthetic automotive intake act: structured damage, custody and photos.

No owner access by VIN. Binary PNG evidence is stored atomically with each
immutable version in the disposable PG17 dev database; no public object URLs.
Only pixel-bearing PNG chunks are retained. Legal signature is NOT implemented.
"""
import base64
import binascii
import hashlib
import json
import struct
import zlib
from uuid import UUID,uuid4
import psycopg

from .auto_workshop_qc import _actor_and_order,InspectionDenied
from .auto_customer_orders import _actor,_BASE,CustomerOrderDenied
from .auto_work_order_estimate import _client_and_scope,AutoEstimateDenied
from .auto_document_access import require_document_access,DocumentPolicyDenied


class IntakeActDenied(ValueError):
    pass


class IntakeActInvalid(ValueError):
    pass


class IntakeActConflict(ValueError):
    pass


AREAS=frozenset(("FRONT","REAR","LEFT","RIGHT","ROOF","CABIN"))
DAMAGE_CODES=frozenset(("SCRATCH","DENT","CRACK","MISSING","CORROSION"))
SEVERITIES=frozenset(("MINOR","MODERATE","SEVERE"))
INVENTORY_CODES=("KEYS","SPARE_WHEEL","TOOLS","DOCS","CHARGER")
INVENTORY_STATES=frozenset(("PRESENT","MISSING","DAMAGED","NOT_CHECKED"))
VEHICLE_KINDS=frozenset(("PASSENGER","SUV","VAN","TRUCK","MOTORCYCLE"))
PNG_SIGNATURE=b"\x89PNG\r\n\x1a\n"
MAX_PNG_BYTES=196608
MAX_PIXEL_COUNT=262144


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    return (struct.pack(">I",len(data))+kind+data+
            struct.pack(">I",binascii.crc32(kind+data)&0xffffffff))


def sanitize_png(data: bytes) -> tuple[bytes,int,int]:
    """CRC-checked, bounded 8-bit noninterlaced RGB(A) PNG, no EXIF/text chunks.

    Rebuild using only IHDR, IDAT and IEND. Decode bounded scanlines to avoid
    decompression bombs; never store GPS/EXIF/comment/ICC chunks or their bytes.
    """
    if type(data) is not bytes or not 0<len(data)<=MAX_PNG_BYTES:
        raise IntakeActInvalid("INVALID_PHOTO")
    if not data.startswith(PNG_SIGNATURE):
        raise IntakeActInvalid("INVALID_PHOTO")
    pos=len(PNG_SIGNATURE)
    header=None
    compressed=[]
    idat_started=False
    idat_done=False
    done=False
    while pos<len(data):
        if len(data)-pos<12:
            raise IntakeActInvalid("INVALID_PHOTO")
        length=struct.unpack(">I",data[pos:pos+4])[0]
        kind=data[pos+4:pos+8]
        end=pos+12+length
        if length>MAX_PNG_BYTES or end>len(data):
            raise IntakeActInvalid("INVALID_PHOTO")
        payload=data[pos+8:pos+8+length]
        expected=struct.unpack(">I",data[pos+8+length:end])[0]
        if binascii.crc32(kind+payload)&0xffffffff != expected:
            raise IntakeActInvalid("INVALID_PHOTO")
        if kind==b'IHDR':
            if header is not None or pos!=len(PNG_SIGNATURE) or length!=13:
                raise IntakeActInvalid("INVALID_PHOTO")
            header=payload
        elif kind==b'IDAT':
            if header is None or idat_done:
                raise IntakeActInvalid("INVALID_PHOTO")
            idat_started=True
            compressed.append(payload)
        elif kind==b'IEND':
            if not idat_started or length!=0 or end!=len(data):
                raise IntakeActInvalid("INVALID_PHOTO")
            done=True
        elif kind in (b'sRGB',b'gAMA',b'cHRM',b'pHYs'):
            # Accept only conventional color/display metadata and discard it.
            if idat_started:
                raise IntakeActInvalid("INVALID_PHOTO")
        else:
            # Reject EXIF, text, embedded profiles, animation, APNG and unknown
            # critical/ancillary chunks rather than retaining hidden metadata.
            raise IntakeActInvalid("INVALID_PHOTO")
        if idat_started and kind!=b'IDAT':
            idat_done=True
        pos=end
        if done:
            break
    if not done or header is None:
        raise IntakeActInvalid("INVALID_PHOTO")
    width,height,depth,color,compression,flt,interlace=struct.unpack(">IIBBBBB",header)
    if (width<1 or height<1 or width>1024 or height>1024 or
            width*height>MAX_PIXEL_COUNT or depth!=8 or
            color not in (2,6) or compression!=0 or flt!=0 or interlace!=0):
        raise IntakeActInvalid("INVALID_PHOTO")
    row_length=width*(3 if color==2 else 4)+1
    expected=row_length*height
    stream=b"".join(compressed)
    try:
        decoder=zlib.decompressobj()
        pixels=decoder.decompress(stream,expected+1)
        pixels+=decoder.flush(expected+1-len(pixels))
    except (zlib.error,ValueError):
        raise IntakeActInvalid("INVALID_PHOTO") from None
    if (len(pixels)!=expected or not decoder.eof or decoder.unused_data or
            decoder.unconsumed_tail or any(pixels[r*row_length]>4
                                           for r in range(height))):
        raise IntakeActInvalid("INVALID_PHOTO")
    clean=PNG_SIGNATURE+_png_chunk(b'IHDR',header)+_png_chunk(b'IDAT',stream)+_png_chunk(b'IEND',b'')
    if len(clean)>MAX_PNG_BYTES:
        raise IntakeActInvalid("INVALID_PHOTO")
    return clean,width,height


def _validate(act: dict) -> tuple[str,list[dict],list[dict],list[dict],list[bytes]]:
    if type(act) is not dict or set(act)!={"vehicleKind","damages","inventory","photos"}:
        raise IntakeActInvalid("INVALID_INPUT")
    kind=act["vehicleKind"]
    damages=act["damages"]
    inventory=act["inventory"]
    photos=act["photos"]
    if (type(kind) is not str or kind not in VEHICLE_KINDS or
            type(damages) is not list or len(damages)>12 or
            type(inventory) is not list or len(inventory)!=5 or
            type(photos) is not list or len(photos)>3):
        raise IntakeActInvalid("INVALID_INPUT")
    if not all(type(d) is dict and set(d)=={"areaCode","damageCode","severity"} and
               d["areaCode"] in AREAS and d["damageCode"] in DAMAGE_CODES and
               d["severity"] in SEVERITIES for d in damages):
        raise IntakeActInvalid("INVALID_INPUT")
    if len({(d["areaCode"],d["damageCode"]) for d in damages})!=len(damages):
        raise IntakeActInvalid("INVALID_INPUT")
    if not all(type(i) is dict and set(i)=={"itemCode","condition"} and
               i["itemCode"] in INVENTORY_CODES and
               i["condition"] in INVENTORY_STATES for i in inventory):
        raise IntakeActInvalid("INVALID_INPUT")
    if {i["itemCode"] for i in inventory}!=set(INVENTORY_CODES):
        raise IntakeActInvalid("INVALID_INPUT")
    if damages and not photos:
        raise IntakeActInvalid("PHOTO_REQUIRED")
    manifest=[]
    blobs=[]
    pixel_digests=set()
    for photo in photos:
        if (type(photo) is not dict or set(photo)!={"areaCode","imageBase64"} or
                photo["areaCode"] not in AREAS or
                type(photo["imageBase64"]) is not str or
                len(photo["imageBase64"])>4*((MAX_PNG_BYTES+2)//3)):
            raise IntakeActInvalid("INVALID_PHOTO")
        try:
            data=base64.b64decode(photo["imageBase64"],validate=True)
        except (ValueError,binascii.Error):
            raise IntakeActInvalid("INVALID_PHOTO") from None
        if base64.b64encode(data).decode("ascii")!=photo["imageBase64"]:
            raise IntakeActInvalid("INVALID_PHOTO")
        clean,width,height=sanitize_png(data)
        digest=hashlib.sha256(clean).hexdigest()
        # SHA сохранённых байтов подтверждает целостность, но не уникальность:
        # повторные PNG с иным zlib-сжатием содержат те же scanlines.
        # sanitize_png уже ограничил размер распаковки и собрал один IDAT.
        pixel_digest=hashlib.sha256(
            clean[16:29]+zlib.decompress(clean[41:-16])
        ).digest()
        if pixel_digest in pixel_digests:
            raise IntakeActInvalid("DUPLICATE_PHOTO")
        pixel_digests.add(pixel_digest)
        blobs.append(clean)
        manifest.append({"areaCode":photo["areaCode"],"mime":"image/png",
                         "sha256":digest,
                         "width":width,"height":height})
    return kind,damages,inventory,manifest,blobs


def _authorize(conn,token,org,loc,order,*,allow_client):
    try:
        actor=_actor_and_order(
            conn,token,org,loc,order,("INTAKE","MANAGER","MECHANIC"))
        require_document_access(conn,token,org,loc,
           ("INTAKE","MANAGER","MECHANIC"),"INTAKE_ACT","READ",order=order)
        return actor
    except DocumentPolicyDenied:
        raise IntakeActDenied("FORBIDDEN") from None
    except InspectionDenied:
        if not allow_client:
            raise IntakeActDenied("FORBIDDEN") from None
    try:
        actor=_actor(conn,token)
    except CustomerOrderDenied:
        raise IntakeActDenied("FORBIDDEN") from None
    row=conn.execute(
        "SELECT w.id "+_BASE+
        " AND w.id=%s AND w.organization_id=%s AND w.location_id=%s"
        " FOR SHARE OF w,a,proof,org,loc,b",
        (actor,order,org,loc),
    ).fetchone()
    if row is None:
        raise IntakeActDenied("FORBIDDEN")
    return actor


def _read(conn,order,org,loc):
    inspection=conn.execute("""
       SELECT id,odometer_km,fuel_percent,exterior_code
       FROM auto_work_order_inspection WHERE work_order_id=%s
    """,(order,)).fetchone()
    rows=conn.execute("""
       SELECT act.id,act.version,act.vehicle_kind,act.damages,act.inventory,
              act.photo_meta,act.fingerprint,act.created_at,act.actor_id,
              review.decision,review.reason_code,review.actor_id,
              review.fingerprint,review.created_at
       FROM auto_work_order_intake_act act
       LEFT JOIN auto_work_order_intake_act_review review ON review.act_id=act.id
       WHERE act.work_order_id=%s
       ORDER BY act.version LIMIT 21
    """,(order,)).fetchall()
    if len(rows)>20:
        raise IntakeActConflict("VERSION_LIMIT")
    versions=[{"id":str(ident),"version":version,"vehicleKind":kind,
       "damages":damages,"inventory":inventory,
       "photos":[{**m,"index":i+1,
           "path":f"/api/v1/dev/auto/organizations/{org}/locations/{loc}/orders/{order}/intake-acts/{ident}/photos/{i+1}"}
           for i,m in enumerate(photo_meta)],
       "sha256":digest,"createdAt":created.isoformat(),
       "actorId":str(actor),
       "review":None if decision is None else {
          "decision":decision,"reasonCode":reason,
          "actorId":str(reviewer),"fingerprint":review_digest,
          "createdAt":reviewed_at.isoformat()}}
       for ident,version,kind,damages,inventory,photo_meta,digest,created,actor,
           decision,reason,reviewer,review_digest,reviewed_at in rows]
    frozen=conn.execute("""
       SELECT EXISTS(SELECT 1 FROM auto_work_order_qc_event WHERE work_order_id=%s),
              EXISTS(SELECT 1 FROM auto_work_order_estimate_revision WHERE work_order_id=%s)
    """,(order,order)).fetchone()
    if any(item["review"] and item["review"]["decision"]=="ACKNOWLEDGED"
           for item in versions):
        frozen=(True,frozen[1])
    # Compare only visits under the EXACT SAME verified customer/vehicle link.
    # Same VIN and same vehicle under a different owner do not match.
    before=conn.execute("""
       SELECT p.work_order_id,p.version,p.damages,p.inventory,p.created_at
       FROM auto_work_order current_order
       JOIN auto_appointment current_appointment
         ON current_appointment.id=current_order.appointment_id
       JOIN auto_work_order previous_order
         ON previous_order.organization_id=current_order.organization_id
         -- A staff grant for the current order never authorizes a visit
         -- to another branch. Until separate cross-branch ACL exists,
         -- comparison history is deliberately limited to this branch.
         AND previous_order.location_id=current_order.location_id
         AND previous_order.id<>current_order.id
         AND previous_order.created_at<current_order.created_at
       JOIN auto_appointment previous_appointment
         ON previous_appointment.id=previous_order.appointment_id
         AND previous_appointment.customer_link_id=current_appointment.customer_link_id
         AND previous_appointment.vehicle_id=current_appointment.vehicle_id
       JOIN auto_work_order_intake_act p ON p.work_order_id=previous_order.id
       WHERE current_order.id=%s
       ORDER BY previous_order.created_at DESC,p.version DESC LIMIT 1
    """,(order,)).fetchone()
    previous=None if before is None else {
       "orderId":str(before[0]),"version":before[1],
       "damages":before[2],"inventory":before[3],
       "createdAt":before[4].isoformat()}
    return {"syntheticOnly":True,"releaseAllowed":False,
            "legalSignatureStatus":"NOT_IMPLEMENTED",
            "inspection":None if not inspection else
              {"id":str(inspection[0]),"odometerKm":inspection[1],
               "fuelPercent":inspection[2],"exteriorCode":inspection[3]},
            "locked":bool(frozen[0] or frozen[1]),"latestVersion":len(versions),
            "previousVisit":previous,"items":versions}


def read_intake_acts(conn,token,org,loc,order):
    with conn.transaction():
        _authorize(conn,token,org,loc,order,allow_client=True)
        return _read(conn,order,org,loc)


def write_intake_act(conn,token,org,loc,order,*,request_key,expected_version,act):
    if (type(request_key) is not UUID or type(expected_version) is not int
            or not 0<=expected_version<20):
        raise IntakeActInvalid("INVALID_INPUT")
    kind,damages,inventory,meta,blobs=_validate(act)
    canonical={"orderId":str(order),"expectedVersion":expected_version,
               "vehicleKind":kind,"damages":damages,"inventory":inventory,
               "photos":meta}
    digest=hashlib.sha256(json.dumps(canonical,sort_keys=True,
                     separators=(',',':')).encode('utf-8')).hexdigest()
    with conn.transaction():
        try:
            actor=_actor_and_order(conn,token,org,loc,order,("INTAKE",))
            require_document_access(conn,token,org,loc,
                                    ("INTAKE",),"INTAKE_ACT","CREATE",order=order)
        except (InspectionDenied,DocumentPolicyDenied):
            raise IntakeActDenied("FORBIDDEN") from None
        existing=conn.execute("""
           SELECT id,work_order_id,actor_id,fingerprint,version
           FROM auto_work_order_intake_act WHERE request_key=%s
        """,(request_key,)).fetchone()
        if existing:
            if existing[1:]!=(order,actor,digest,expected_version+1):
                raise IntakeActConflict("KEY_REUSED")
            return {"ok":True,"replayed":True,**_read(conn,order,org,loc)}
        snapshot=_read(conn,order,org,loc)
        if snapshot["inspection"] is None:
            raise IntakeActConflict("INSPECTION_REQUIRED")
        if snapshot["locked"]:
            raise IntakeActConflict("ACT_FROZEN")
        if snapshot["latestVersion"]!=expected_version:
            raise IntakeActConflict("STALE_VERSION")
        if damages and snapshot["inspection"]["exteriorCode"]=="CLEAR":
            raise IntakeActConflict("INSPECTION_CONTRADICTS_DAMAGES")
        try:
            with conn.transaction():
                conn.execute("""
                   INSERT INTO auto_work_order_intake_act
                   (id,work_order_id,inspection_id,version,actor_id,request_key,
                    fingerprint,vehicle_kind,damages,inventory,photo_meta,photo_bytes)
                   VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s::bytea[])
                """,(uuid4(),order,UUID(snapshot["inspection"]["id"]),
                      expected_version+1,actor,request_key,digest,kind,
                      json.dumps(damages),json.dumps(inventory),json.dumps(meta),blobs))
        except (psycopg.errors.RaiseException,psycopg.errors.UniqueViolation,
                psycopg.errors.CheckViolation) as exc:
            raise IntakeActConflict("ACT_CONFLICT") from exc
        return {"ok":True,"replayed":False,**_read(conn,order,org,loc)}


def read_act_photo(conn,token,org,loc,order,act_id,index):
    if any(type(x) is not UUID for x in (org,loc,order,act_id)) or (
            type(index) is not int or index<1 or index>3):
        raise IntakeActInvalid("INVALID_INPUT")
    with conn.transaction():
        _authorize(conn,token,org,loc,order,allow_client=True)
        row=conn.execute("""
           SELECT photo_bytes[%s],photo_meta->%s FROM auto_work_order_intake_act
           WHERE id=%s AND work_order_id=%s
        """,(index,index-1,act_id,order)).fetchone()
        if row is None or row[0] is None or row[1] is None:
            raise IntakeActDenied("FORBIDDEN")
        payload,meta=row
        if hashlib.sha256(payload).hexdigest()!=meta["sha256"]:
            raise IntakeActConflict("PHOTO_INTEGRITY")
        return payload


def review_intake_act(conn,token,org,loc,order,act_id,*,request_key,
                      fingerprint,decision,reason_code):
    """CLIENT-only immutable review; neither legal signature nor repair approval.

    The original owner/session and WorkOrder row are locked before inspecting
    the latest act. All exact replays are authorized again after revocation.
    """
    if (any(type(x) is not UUID for x in (org,loc,order,act_id,request_key))
            or type(fingerprint) is not str or len(fingerprint)!=64
            or any(ch not in "0123456789abcdef" for ch in fingerprint)
            or decision not in ("ACKNOWLEDGED","DISPUTED")
            or not ((decision=="ACKNOWLEDGED" and reason_code=="NONE")
                    or (decision=="DISPUTED" and reason_code in
                        ("DAMAGE_MISMATCH","INVENTORY_MISMATCH","OTHER")))):
        raise IntakeActInvalid("INVALID_INPUT")
    with conn.transaction():
        try:
            actor=_client_and_scope(conn,token,org,loc,order)
        except AutoEstimateDenied:
            raise IntakeActDenied("FORBIDDEN") from None
        existing=conn.execute("""
          SELECT work_order_id,act_id,actor_id,fingerprint,decision,reason_code
          FROM auto_work_order_intake_act_review WHERE request_key=%s
        """,(request_key,)).fetchone()
        if existing:
            if existing!=(order,act_id,actor,fingerprint,decision,reason_code):
                raise IntakeActConflict("KEY_REUSED")
            return {"ok":True,"replayed":True,**_read(conn,order,org,loc)}
        latest=conn.execute("""
          SELECT id,fingerprint FROM auto_work_order_intake_act
          WHERE work_order_id=%s ORDER BY version DESC LIMIT 1
        """,(order,)).fetchone()
        if latest is None or latest!=(act_id,fingerprint):
            raise IntakeActConflict("STALE_ACT")
        if conn.execute("""
           SELECT 1 FROM auto_work_order_intake_act_review WHERE act_id=%s
        """,(act_id,)).fetchone():
            raise IntakeActConflict("ALREADY_REVIEWED")
        try:
            with conn.transaction():
                conn.execute("""
                   INSERT INTO auto_work_order_intake_act_review
                   (id,work_order_id,act_id,actor_id,request_key,
                    fingerprint,decision,reason_code)
                   VALUES(%s,%s,%s,%s,%s,%s,%s,%s)
                """,(uuid4(),order,act_id,actor,request_key,
                     fingerprint,decision,reason_code))
        except (psycopg.errors.RaiseException,psycopg.errors.UniqueViolation,
                psycopg.errors.CheckViolation,psycopg.errors.ForeignKeyViolation):
            raise IntakeActConflict("REVIEW_CONFLICT") from None
        return {"ok":True,"replayed":False,**_read(conn,order,org,loc)}
