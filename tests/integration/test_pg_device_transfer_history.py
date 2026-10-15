"""Device ownership transfer must not transfer historic ticket/attachment rights."""
from datetime import datetime,timedelta,timezone
from uuid import uuid4
import psycopg
from roboport_api.document_access import authorize_attachment_read
from roboport_api.authorization import authorize_ticket
from roboport_api.session_tokens import new_token,token_digest
from test_pg_baseline import DSN


def test_new_device_owner_cannot_read_previous_owner_history():
    device,customer_a,customer_b,user_a,user_b=(uuid4() for _ in range(5))
    ticket_a,ticket_b,attachment_a=(uuid4() for _ in range(3))
    token_a,token_b=new_token(),new_token()
    now=datetime.now(timezone.utc)
    with psycopg.connect(DSN) as db:
        db.execute("INSERT INTO robot_device(id,serial_number) VALUES(%s,%s)",
                   (device,"synthetic-"+device.hex))
        for customer,user,token in ((customer_a,user_a,token_a),(customer_b,user_b,token_b)):
            db.execute("INSERT INTO customer(id,label) VALUES(%s,%s)",
                       (customer,customer.hex))
            db.execute("INSERT INTO app_user(id,normalized_login) VALUES(%s,%s)",
                       (user,"transfer-"+user.hex))
            db.execute("INSERT INTO user_role(id,user_id,role_code) VALUES(%s,%s,'CLIENT')",
                       (uuid4(),user))
            db.execute("INSERT INTO account_customer_link(user_id,customer_id) VALUES(%s,%s)",
                       (user,customer))
            db.execute("""INSERT INTO user_session(id,user_id,token_hash,csrf_hash,created_at,idle_expires_at,absolute_expires_at)
                          VALUES(%s,%s,%s,%s,%s,%s,%s)""",
                       (uuid4(),user,token_digest(token),token_digest(new_token()),
                        now,now+timedelta(minutes=30),now+timedelta(hours=4)))
        for ticket,customer in ((ticket_a,customer_a),(ticket_b,customer_b)):
            db.execute("INSERT INTO service_ticket(id,customer_id,device_id) VALUES(%s,%s,%s)",
                       (ticket,customer,device))
        db.execute("""INSERT INTO attachment(id,ticket_id,object_key,mime,byte_size,sha256,created_by,upload_state)
                      VALUES(%s,%s,%s,'text/plain',1,%s,%s,'READY')""",
                   (attachment_a,ticket_a,uuid4().hex,"a"*64,user_a))
        assert authorize_ticket(db,token_a,"ticket.read",ticket_a)
        assert authorize_ticket(db,token_b,"ticket.read",ticket_b)
        assert not authorize_ticket(db,token_b,"ticket.read",ticket_a)
        assert authorize_attachment_read(db,token_b,attachment_a) is None
        # Simulate end of previous ownership; new customer still owns device
        # only by its new ticket, never by inheriting customer A's history.
        db.execute("UPDATE account_customer_link SET revoked_at=now() WHERE user_id=%s",(user_a,))
        assert not authorize_ticket(db,token_a,"ticket.read",ticket_a)
        assert not authorize_ticket(db,token_b,"ticket.read",ticket_a)
        assert authorize_attachment_read(db,token_b,attachment_a) is None
