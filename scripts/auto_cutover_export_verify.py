"""Offline integrity verification of a synthetic cutover export (no DB writes).

Usage: PYTHONPATH=apps/api:. python scripts/auto_cutover_export_verify.py --synthetic-only --expect-manifest-sha256 DIGEST DIR
"""
import argparse
import json
from pathlib import Path

from roboport_api.auto_synthetic_cutover import SyntheticCutoverDenied
from roboport_api.auto_synthetic_cutover_export import verify_synthetic_cutover_export


def main():
    parser=argparse.ArgumentParser(
        description="Проверка переноса synthetic VIN/SERVICE/SKU без подключения к БД",
    )
    parser.add_argument("--synthetic-only",action="store_true",required=True)
    mode=parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--expect-manifest-sha256", metavar="SHA256")
    mode.add_argument("--structure-only", action="store_true",
                      help="Диагностика структуры без подтверждения исходного манифеста")
    parser.add_argument("directory",type=Path)
    args=parser.parse_args()
    try:
        report=verify_synthetic_cutover_export(
            args.directory, expected_manifest_sha256=args.expect_manifest_sha256,
            structure_only=args.structure_only,
        )
    except SyntheticCutoverDenied as exc:
        code = "MANIFEST_DIGEST_REQUIRED" if str(exc) == "MANIFEST_DIGEST_REQUIRED" else "INVALID_EXPORT"
        parser.error(code + ": не прошла проверка пакета")
    # Do not print source IDs, VIN, articles, file paths or raw CSV content.
    print(json.dumps(report,sort_keys=True,ensure_ascii=False))


if __name__=="__main__":
    main()
