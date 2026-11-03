"""Offline integrity verification of a synthetic cutover export (no DB writes).

Usage: PYTHONPATH=apps/api:. python scripts/auto_cutover_export_verify.py --synthetic-only DIR
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
    parser.add_argument("directory",type=Path)
    args=parser.parse_args()
    try:
        report=verify_synthetic_cutover_export(args.directory)
    except SyntheticCutoverDenied:
        parser.error("INVALID_EXPORT: не прошла проверка пакета")
    # Do not print source IDs, VIN, articles, file paths or raw CSV content.
    print(json.dumps(report,sort_keys=True,ensure_ascii=False))


if __name__=="__main__":
    main()
