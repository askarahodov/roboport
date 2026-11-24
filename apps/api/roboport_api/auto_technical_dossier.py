"""Проверяемый архив технически закрытого автомобильного заказ-наряда.

Только закрытый синтетический стенд: без юридической подписи, фискальных
документов, реальной оплаты или разрешения физической выдачи автомобиля.
"""
from hashlib import sha256
from io import BytesIO
import json
from uuid import UUID
from zipfile import ZipFile, ZipInfo, ZIP_STORED

from .auto_demo_close import _manager, _state_snapshot, DemoCloseDenied, DemoCloseConflict
from .auto_work_order_estimate import _client_and_scope, AutoEstimateDenied
from .auto_org_authority import organization_role_allows
from .auto_intake_act import (
    export_intake_act_evidence, IntakeActDenied, IntakeActConflict,
)


class TechnicalDossierDenied(ValueError):
    pass


class TechnicalDossierConflict(ValueError):
    pass


def _json_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def export_closed_technical_dossier(conn, token, org, loc, order):
    """Собрать один детерминированный пакет по закрытому техническому журналу.

    Контроль доступа и чтение исходных версий выполняются в одной
    согласованной транзакции. Снимок никогда не является подписанным актом.
    """
    if any(type(value) is not UUID for value in (org, loc, order)):
        raise TechnicalDossierDenied("FORBIDDEN")
    with conn.transaction():
        # Все чтения, включая вложенный ZIP акта, должны видеть один снимок.
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        # A denied staff grant must not silently switch to the owner path.
        # This matches the normal handover read; only a non-staff original
        # verified customer can use the separate owner authorization.
        if any(organization_role_allows(conn,token,org,role,loc,for_mutation=True)
               for role in ("MANAGER","WAREHOUSE")):
            try:
                _manager(conn,token,org,loc,order,cap="READ")
            except DemoCloseDenied:
                raise TechnicalDossierDenied("FORBIDDEN") from None
        else:
            try:
                _client_and_scope(conn,token,org,loc,order)
            except AutoEstimateDenied:
                raise TechnicalDossierDenied("FORBIDDEN") from None

        state = _state_snapshot(conn, order)
        workflow = state["documentWorkflow"]
        if workflow["status"] != "CLOSED_TECHNICAL":
            raise TechnicalDossierConflict("TECHNICAL_CLOSE_REQUIRED")
        preview = state["preview"]
        act = workflow["intakeAct"]
        events = workflow["events"]
        if (preview is None or act is None or
                act["clientDecision"] != "ACKNOWLEDGED" or
                len(events) != 3 or
                [event["action"] for event in events] !=
                ["POSTED", "CLIENT_ACK", "CLOSED_TECHNICAL"] or
                any(event["previewId"] != preview["id"] or
                    event["previewDigest"] != preview["digest"] or
                    event["intakeActId"] != act["id"] or
                    event["intakeDigest"] != act["digest"] for event in events)):
            raise TechnicalDossierConflict("DOCUMENT_INTEGRITY")

        # SHA считается PostgreSQL по каноническому тексту jsonb, а не по
        # произвольной сериализации Python; неверный fingerprint — отказ.
        checked = conn.execute("""
            SELECT encode(sha256(convert_to(snapshot::text,'UTF8')),'hex')
            FROM auto_order_demo_handover_preview
            WHERE work_order_id=%s AND id=%s
        """, (order, UUID(preview["id"]))).fetchone()
        if checked is None or checked[0] != preview["digest"]:
            raise TechnicalDossierConflict("DOCUMENT_INTEGRITY")

        try:
            intake_zip = export_intake_act_evidence(
                conn, token, org, loc, order, UUID(act["id"]))
        except IntakeActDenied:
            raise TechnicalDossierDenied("FORBIDDEN") from None
        except IntakeActConflict:
            raise TechnicalDossierConflict("PHOTO_INTEGRITY") from None

        # Вложенный экспорт уже проверяет SHA каждого PNG. Дополнительно
        # проверяем, что это ровно та версия, на которую ссылается закрытие.
        with ZipFile(BytesIO(intake_zip)) as nested:
            intake_manifest = json.loads(nested.read("manifest.json"))
        if (intake_manifest["act"]["id"] != act["id"] or
                intake_manifest["act"]["sha256"] != act["digest"] or
                intake_manifest["act"]["review"] is None or
                intake_manifest["act"]["review"]["decision"] != "ACKNOWLEDGED"):
            raise TechnicalDossierConflict("DOCUMENT_INTEGRITY")

        settlement = {
            "syntheticOnly": True,
            "currency": "RUB",
            "approvedRevisionId": state["approvedRevisionId"],
            "approvedBaseMinor": state["approvedBaseMinor"],
            "approvedExtraMinor": state["approvedExtraMinor"],
            "approvedTotalMinor": state["approvedTotalMinor"],
            "simulatedPaidMinor": state["simulatedPaidMinor"],
            "simulatedRefundedMinor": state["simulatedRefundedMinor"],
            "simulatedDueMinor": state["simulatedDueMinor"],
            "simulatedCashEvents": state["events"],
            "executionStatus": state["executionStatus"],
            "outstandingReserves": state["outstandingReserves"],
            "unreceivedMaterialCount": state["unreceivedMaterialCount"],
        }
        if (settlement["simulatedDueMinor"] != 0 or
                settlement["executionStatus"] != "QC_ACCEPTED" or
                settlement["outstandingReserves"] or
                settlement["unreceivedMaterialCount"]):
            raise TechnicalDossierConflict("SETTLEMENT_INTEGRITY")

        files = [
            ("preview.json", _json_bytes(preview["snapshot"])),
            ("document-events.json", _json_bytes(events)),
            ("settlement.json", _json_bytes(settlement)),
            ("intake-evidence.zip", intake_zip),
        ]
        manifest = {
            "schemaVersion": 1,
            "syntheticOnly": True,
            "releaseAllowed": False,
            "legalSignatureStatus": "NOT_IMPLEMENTED",
            "physicalHandover": False,
            "documentStatus": "CLOSED_TECHNICAL",
            "organizationId": str(org),
            "locationId": str(loc),
            "workOrderId": str(order),
            "previewId": preview["id"],
            "previewDigest": preview["digest"],
            "intakeActId": act["id"],
            "intakeDigest": act["digest"],
            "files": [
                {"path": name, "sha256": sha256(payload).hexdigest(),
                 "bytes": len(payload)}
                for name, payload in files
            ],
        }
        output = BytesIO()
        with ZipFile(output, "w", compression=ZIP_STORED, allowZip64=False) as archive:
            for name, payload in [("manifest.json", _json_bytes(manifest)), *files]:
                info = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = ZIP_STORED
                info.external_attr = 0o600 << 16
                archive.writestr(info, payload)
        if output.tell() > 1024 * 1024:
            raise TechnicalDossierConflict("DOSSIER_TOO_LARGE")
        return output.getvalue()
