"""Disposable PostgreSQL: attachment metadata must remain immutable and quarantined."""
from uuid import uuid4
import psycopg
import pytest
from test_pg_baseline import DSN


def test_attachment_metadata_invariants_and_quarantine():
    actor,customer,ticket,attachment=(uuid4() for _ in range(4))
    key=uuid4().hex
    with psycopg.connect(DSN) as db:
        db.execute("INSERT INTO app_user(id,normalized_login) VALUES(%s,%s)",
                   (actor,"attachment-"+actor.hex))
        db.execute("INSERT INTO customer(id,label) VALUES(%s,%s)",
                   (customer,"synthetic-"+customer.hex))
        db.execute("INSERT INTO service_ticket(id,customer_id) VALUES(%s,%s)",
                   (ticket,customer))
        db.execute("""INSERT INTO attachment(id,ticket_id,object_key,mime,byte_size,sha256,created_by)
                      VALUES(%s,%s,%s,'text/plain',17,%s,%s)""",
                   (attachment,ticket,key,"a"*64,actor))
        assert db.execute("SELECT upload_state FROM attachment WHERE id=%s",
                          (attachment,)).fetchone()[0]=="PENDING"
        db.execute("UPDATE attachment SET upload_state='READY' WHERE id=%s",(attachment,))
        db.execute("UPDATE attachment SET quarantine_state='QUARANTINED' WHERE id=%s",
                   (attachment,))
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("UPDATE attachment SET quarantine_state='CLEAR' WHERE id=%s",
                           (attachment,))
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("UPDATE attachment SET object_key=%s WHERE id=%s",
                           (uuid4().hex,attachment))
        db.execute("UPDATE attachment SET upload_state='DELETED' WHERE id=%s",(attachment,))
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("UPDATE attachment SET upload_state='READY' WHERE id=%s",
                           (attachment,))


def test_attachment_rejects_bad_size_mime_and_hash():
    actor,customer,ticket=(uuid4() for _ in range(3))
    with psycopg.connect(DSN) as db:
        db.execute("INSERT INTO app_user(id,normalized_login) VALUES(%s,%s)",
                   (actor,"file-constraint-"+actor.hex))
        db.execute("INSERT INTO customer(id,label) VALUES(%s,%s)",(customer,"synthetic"))
        db.execute("INSERT INTO service_ticket(id,customer_id) VALUES(%s,%s)",(ticket,customer))
        for mime,size,digest in (("text/html",1,"a"*64),
                                 ("text/plain",0,"a"*64),
                                 ("text/plain",8388609,"a"*64),
                                 ("text/plain",1,"not-a-hash")):
            with pytest.raises(psycopg.errors.CheckViolation):
                with db.transaction():
                    db.execute("""INSERT INTO attachment(id,ticket_id,object_key,mime,byte_size,sha256,created_by)
                                  VALUES(%s,%s,%s,%s,%s,%s,%s)""",
                               (uuid4(),ticket,uuid4().hex,mime,size,digest,actor))
