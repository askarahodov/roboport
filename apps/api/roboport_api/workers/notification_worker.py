"""One-shot synthetic outbox runner. No external notification transport."""
import argparse
import os
from urllib.parse import urlsplit
import psycopg
from ..notification_outbox import run_batch


def main():
    parser=argparse.ArgumentParser(description="ROBOport disposable DEV_SINK worker")
    parser.add_argument("--worker-id",required=True)
    parser.add_argument("--limit",type=int,default=20)
    args=parser.parse_args()
    dsn=os.getenv("ROBOport_DATABASE_URL","")
    try:
        url=urlsplit(dsn)
        valid=(os.getenv("ROBOport_SYNTHETIC_ACCESS_API")=="1"
               and url.scheme in ("postgresql","postgresql+psycopg")
               and url.hostname=="127.0.0.1" and url.port is not None)
    except ValueError:
        valid=False
    if not valid:
        parser.error("worker requires explicit synthetic opt-in and loopback PostgreSQL")
    with psycopg.connect(dsn.replace("postgresql+psycopg://","postgresql://",1)) as conn:
        batch=run_batch(conn,args.worker_id,args.limit)
    # Only synthetic IDs; no payloads, tokens or customer PII.
    print(f"DEV_SINK processed: {len(batch)}")


if __name__=="__main__":
    main()
