"""Offline verifier: complete valid synthetic dossier and tampering failures."""
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4
from zipfile import ZipFile, ZIP_STORED, ZIP_DEFLATED

from scripts.verify_auto_technical_dossier import (
    DossierVerificationError, main, verify_dossier_bytes,
)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def _zip(files, *, compression=ZIP_STORED):
    output = BytesIO()
    with ZipFile(output, "w", compression=compression) as archive:
        for name, data in files:
            archive.writestr(name, data)
    return output.getvalue()


def dossier(*, tamper=None, nested_tamper=None):
    org, loc, order, preview_id, act_id, rev = [str(uuid4()) for _ in range(6)]
    digest_preview, digest_act = "a" * 64, "b" * 64
    png = b"\x89PNG\r\n\x1a\n" + b"synthetic-pixels"
    nested_manifest = {
        "schemaVersion": 1, "syntheticOnly": True, "releaseAllowed": False,
        "legalSignatureStatus": "NOT_IMPLEMENTED", "organizationId": org,
        "locationId": loc, "orderId": order,
        "act": {"id": act_id, "sha256": digest_act,
                "review": {"decision": "ACKNOWLEDGED"},
                "photos": [{"sha256": sha256(png).hexdigest(),
                            "areaCode": "FRONT", "width": 2, "height": 2}]},
        "files": [{"path": "photos/1.png", "sha256": sha256(png).hexdigest(),
                   "areaCode": "FRONT", "width": 2, "height": 2}],
    }
    if nested_tamper == "photo":
        png += b"changed"
    if nested_tamper == "review":
        nested_manifest["act"]["review"]["decision"] = "DISPUTED"
    if nested_tamper == "photo-meta":
        nested_manifest["act"]["photos"][0]["sha256"] = "f" * 64
    if nested_tamper == "missing-photo-meta":
        nested_manifest["act"]["photos"].clear()
    if nested_tamper == "file-entry-null":
        nested_manifest["files"][0] = None
    intake = _zip([("manifest.json", _json(nested_manifest)), ("photos/1.png", png)])
    preview = {
        "kind": "SYNTHETIC_PRE_HANDOVER_PREVIEW", "workOrderId": order,
        "estimateRevisionId": rev, "currency": "RUB", "releaseAllowed": False,
        "legalGatePassed": False, "independentQc": "PASS",
        "stockReservations": 0, "unreceivedMaterials": 0,
        "approvedBaseMinor": 500, "approvedExtraMinor": 100,
        "approvedTotalMinor": 600, "simulatedPaidMinor": 700,
        "simulatedRefundedMinor": 100, "simulatedDueMinor": 0,
        "extraLines": [{"amountMinor": 100}],
    }
    events = [{
        "id": str(uuid4()), "actorId": str(uuid4()), "action": action,
        "previewId": preview_id, "previewDigest": digest_preview,
        "intakeActId": act_id, "intakeDigest": digest_act,
    } for action in ("POSTED", "CLIENT_ACK", "CLOSED_TECHNICAL")]
    settlement = {
        "syntheticOnly": True, "currency": "RUB",
        "approvedRevisionId": rev, "executionStatus": "QC_ACCEPTED",
        "outstandingReserves": [], "unreceivedMaterialCount": 0,
        **{k: preview[k] for k in (
            "approvedBaseMinor", "approvedExtraMinor", "approvedTotalMinor",
            "simulatedPaidMinor", "simulatedRefundedMinor", "simulatedDueMinor")},
        "simulatedCashEvents": [
            {"id": str(uuid4()), "kind": "SIM_PAYMENT", "amountMinor": 700},
            {"id": str(uuid4()), "kind": "SIM_REFUND", "amountMinor": 100},
        ],
    }
    if tamper == "release":
        preview["releaseAllowed"] = True
    if tamper == "events":
        events[1]["intakeDigest"] = "c" * 64
    if tamper == "settlement":
        settlement["simulatedDueMinor"] = 100
    if tamper == "extra-total":
        preview["extraLines"][0]["amountMinor"] = 90
    if tamper == "missing-extra":
        preview["extraLines"].clear()
    files = [("preview.json", _json(preview)),
             ("document-events.json", _json(events)),
             ("settlement.json", _json(settlement)),
             ("intake-evidence.zip", intake)]
    if tamper == "filehash":
        first = files[0]
        files[0] = (first[0], first[1] + b" ")
    manifest = {
        "schemaVersion": 1, "syntheticOnly": True,
        "releaseAllowed": False, "physicalHandover": False,
        "legalSignatureStatus": "NOT_IMPLEMENTED",
        "documentStatus": "CLOSED_TECHNICAL",
        "organizationId": org, "locationId": loc,
        "workOrderId": order, "previewId": preview_id,
        "previewDigest": digest_preview,
        "intakeActId": act_id, "intakeDigest": digest_act,
        "files": [{"path": name, "bytes": len(data),
                   "sha256": sha256(data).hexdigest()}
                  for name, data in files],
    }
    if tamper == "filehash":
        manifest["files"][0]["sha256"] = "0" * 64
    return _zip([("manifest.json", _json(manifest)), *files])


class VerifyTechnicalDossierTest(unittest.TestCase):
    def test_complete_nested_dossier_and_cli(self):
        raw = dossier()
        self.assertEqual(verify_dossier_bytes(raw), {
            "verified": True, "schemaVersion": 1,
            "documentStatus": "CLOSED_TECHNICAL", "syntheticOnly": True,
            "releaseAllowed": False, "documentEvents": 3, "photos": 1,
        })
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder) / "dossier.zip"
            file.write_bytes(raw)
            self.assertEqual(main([str(file), "--json"]), 0)
            file.write_bytes(b"not-a-zip")
            self.assertEqual(main([str(file), "--json"]), 1)

    def test_separately_pinned_digest_rejects_whole_archive_substitution(self):
        original = dossier()
        pinned = sha256(original).hexdigest()
        self.assertTrue(verify_dossier_bytes(original, expected_sha256=pinned)["verified"])
        substituted = dossier()  # New internally consistent ZIP with all hashes recomputed.
        self.assertTrue(verify_dossier_bytes(substituted)["verified"])
        with self.assertRaisesRegex(DossierVerificationError, "ARCHIVE_SHA256_MISMATCH"):
            verify_dossier_bytes(substituted, expected_sha256=pinned)
        for invalid in ("", "z" * 64, pinned.upper(), "0" * 63):
            with self.subTest(expected_sha256=invalid):
                with self.assertRaisesRegex(DossierVerificationError, "INVALID_EXPECTED_SHA256"):
                    verify_dossier_bytes(original, expected_sha256=invalid)
        with tempfile.TemporaryDirectory() as folder:
            archive = Path(folder) / "dossier.zip"
            archive.write_bytes(original)
            self.assertEqual(main([str(archive), "--expected-sha256", pinned, "--json"]), 0)
            archive.write_bytes(substituted)
            self.assertEqual(main([str(archive), "--expected-sha256", pinned, "--json"]), 1)
            self.assertEqual(main([str(archive), "--expected-sha256", "bogus", "--json"]), 1)

    def test_rejects_internal_digest_and_cross_file_tampering(self):
        for kind in ("release", "events", "settlement", "filehash", "extra-total", "missing-extra"):
            with self.subTest(kind=kind):
                with self.assertRaises(DossierVerificationError):
                    verify_dossier_bytes(dossier(tamper=kind))

    def test_rejects_nested_photo_and_disputed_act_with_valid_outer_hashes(self):
        for kind in ("photo", "review", "photo-meta", "missing-photo-meta", "file-entry-null"):
            with self.subTest(kind=kind):
                with self.assertRaises(DossierVerificationError):
                    verify_dossier_bytes(dossier(nested_tamper=kind))

    def test_rejects_duplicates_compression_and_size(self):
        valid = dossier()
        with ZipFile(BytesIO(valid)) as z:
            entries = [(n, z.read(n)) for n in z.namelist()]
        for invalid in (
            _zip(entries + [entries[-1]]),
            _zip(entries, compression=ZIP_DEFLATED),
            valid + b" " * (1024 * 1024),
        ):
            with self.assertRaises(DossierVerificationError):
                verify_dossier_bytes(invalid)


if __name__ == "__main__":
    unittest.main()
