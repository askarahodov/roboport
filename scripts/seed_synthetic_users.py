"""Explicit disposable dev seed: two clients and one staff admin.

No default passwords, emails, addresses, public registration or real data.
Run only with explicit synthetic opt-in and PostgreSQL on 127.0.0.1.
"""
import argparse
import os
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL,uuid4,uuid5
import psycopg
from roboport_api.passwords import hash_password


def main():
    parser=argparse.ArgumentParser(description="ROBOport synthetic local identities")
    parser.add_argument("--i-understand-dev-only",action="store_true",required=True)
    opts=parser.parse_args()
    dsn=os.environ.get("ROBOport_DATABASE_URL","")
    try:
        target=urlsplit(dsn)
        ok=(target.scheme in ("postgresql","postgresql+psycopg")
            and target.hostname=="127.0.0.1" and target.port is not None
            and os.environ.get("ROBOport_SYNTHETIC_AUTH_API")=="1")
    except ValueError:
        ok=False
    if not ok or not opts.i_understand_dev_only:
        parser.error("seed requires synthetic opt-in and loopback disposable PostgreSQL")
    accounts=[
        ("synthetic-customer-a","CLIENT","ROBOport_SEED_CLIENT_A_PASSWORD"),
        ("synthetic-customer-b","CLIENT","ROBOport_SEED_CLIENT_B_PASSWORD"),
        ("synthetic-staff-admin","ADMIN","ROBOport_SEED_ADMIN_PASSWORD"),
    ]
    passwords=[]
    for _,_,var in accounts:
        value=os.environ.get(var,"")
        if len(value)<12 or len(value)>1024:
            parser.error(f"{var} must be supplied, 12..1024 characters")
        passwords.append(value)
    if len(set(passwords))!=len(passwords):
        parser.error("each synthetic account needs a distinct password")
    with psycopg.connect(dsn.replace("postgresql+psycopg://","postgresql://",1)) as conn:
        for (login,role,_),password in zip(accounts,passwords):
            uid=uuid5(NAMESPACE_URL,"roboport-dev-only://"+login)
            exists=conn.execute("SELECT 1 FROM app_user WHERE id=%s",(uid,)).fetchone()
            if not exists:
                conn.execute("INSERT INTO app_user(id,normalized_login) VALUES(%s,%s)",(uid,login))
                conn.execute("""INSERT INTO local_credential(user_id,password_hash,must_change_password)
                                VALUES(%s,%s,false)""",(uid,hash_password(password)))
            if not conn.execute("SELECT 1 FROM user_role WHERE user_id=%s AND role_code=%s",(uid,role)).fetchone():
                conn.execute("INSERT INTO user_role(id,user_id,role_code) VALUES(%s,%s,%s)",
                             (uuid4(),uid,role))
            if role=="CLIENT":
                customer=uuid5(NAMESPACE_URL,"roboport-dev-customer://"+login)
                conn.execute("""INSERT INTO customer(id,label)
                                VALUES(%s,%s) ON CONFLICT(id) DO NOTHING""",
                             (customer,"Synthetic "+login))
                conn.execute("""INSERT INTO account_customer_link(user_id,customer_id)
                                VALUES(%s,%s) ON CONFLICT(user_id,customer_id) DO NOTHING""",
                             (uid,customer))
    print("Created or verified three disposable synthetic identities; credentials not printed.")


if __name__=="__main__":
    main()
