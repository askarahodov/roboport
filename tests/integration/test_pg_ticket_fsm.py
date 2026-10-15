"""Real PG FSM transition, version and immutable event regression."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4
import psycopg
import pytest
from roboport_api.session_tokens import new_token, token_digest
from roboport_api.ticket_fsm import transition_ticket, TransitionConflict
from test_pg_baseline import DSN


def test_staff_intake_transition_version_audit_and_safety_bundle():
    user, customer, ticket, session, role, acl = (uuid4() for _ in range(6))
    token = new_token()
    now = datetime.now(timezone.utc)
    with psycopg.connect(DSN) as db:
        db.execute("INSERT INTO app_user(id,normalized_login) VALUES(%s,%s)",
                   (user,"fsm-"+user.hex))
        db.execute("INSERT INTO customer(id,label) VALUES(%s,%s)",
                   (customer,customer.hex))
        db.execute("INSERT INTO service_ticket(id,customer_id) VALUES(%s,%s)",
                   (ticket,customer))
        db.execute("""INSERT INTO user_session(id,user_id,token_hash,csrf_hash,created_at,idle_expires_at,absolute_expires_at)
                       VALUES(%s,%s,%s,%s,%s,%s,%s)""",
                   (session,user,token_digest(token),token_digest(new_token()),now,
                    now+timedelta(minutes=30),now+timedelta(hours=4)))
        db.execute("INSERT INTO user_role(id,user_id,role_code) VALUES(%s,%s,'INTAKE')",
                   (role,user))
        db.execute("INSERT INTO acl_entry(id,user_id,ticket_id,action) VALUES(%s,%s,%s,'ticket.intake')",
                   (acl,user,ticket))
    with psycopg.connect(DSN) as db:
        # 1 legal change; 2 event actor; 3 optimistic version; 4 immutable audit
        result=transition_ticket(db,token,ticket,"SUBMITTED",1)
        assert result["version"]==2 and result["state"]=="SUBMITTED"
        events=db.execute("""SELECT from_state,to_state,expected_version,actor_id FROM ticket_state_event
                              WHERE ticket_id=%s""",(ticket,)).fetchall()
        assert events==[("DRAFT","SUBMITTED",1,user)]
        with pytest.raises(TransitionConflict):
            transition_ticket(db,token,ticket,"TRIAGE",1)
        with pytest.raises(TransitionConflict):
            transition_ticket(db,token,ticket,"COMPLETED",2)
        # 5 next legal transition; 6 revoke immediately blocks mutation.
        assert transition_ticket(db,token,ticket,"TRIAGE",2)["version"]==3
        db.execute("UPDATE acl_entry SET revoked_at=now() WHERE id=%s",(acl,))
        with pytest.raises(TransitionConflict):
            transition_ticket(db,token,ticket,"AWAITING_DELIVERY",3)
        assert db.execute("SELECT state,version FROM service_ticket WHERE id=%s",
                          (ticket,)).fetchone()==("TRIAGE",3)
        event_id=db.execute("SELECT id FROM ticket_state_event WHERE ticket_id=%s LIMIT 1",
                            (ticket,)).fetchone()[0]
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("DELETE FROM ticket_state_event WHERE id=%s",(event_id,))
        # 7 Anonymous token denied; 8 QC bypass denied by graph.
        with pytest.raises(TransitionConflict):
            transition_ticket(db,new_token(),ticket,"AWAITING_DELIVERY",3)


def test_safety_and_qc_guard_deny_illegal_shortcuts():
    user,customer,ticket,session,role,acl=(uuid4() for _ in range(6))
    token=new_token()
    now=datetime.now(timezone.utc)
    with psycopg.connect(DSN) as db:
        db.execute("INSERT INTO app_user(id,normalized_login) VALUES(%s,%s)",
                   (user,"fsm-engineer-"+user.hex))
        db.execute("INSERT INTO customer(id,label) VALUES(%s,%s)",(customer,customer.hex))
        db.execute("INSERT INTO service_ticket(id,customer_id,state) VALUES(%s,%s,'SAFETY_HOLD')",
                   (ticket,customer))
        db.execute("""INSERT INTO user_session(id,user_id,token_hash,csrf_hash,created_at,idle_expires_at,absolute_expires_at)
                      VALUES(%s,%s,%s,%s,%s,%s,%s)""",
                   (session,user,token_digest(token),token_digest(new_token()),now,
                    now+timedelta(minutes=30),now+timedelta(hours=4)))
        db.execute("INSERT INTO user_role(id,user_id,role_code) VALUES(%s,%s,'ENGINEER')",
                   (role,user))
        db.execute("INSERT INTO acl_entry(id,user_id,ticket_id,action) VALUES(%s,%s,%s,'ticket.diagnose')",
                   (acl,user,ticket))
    with psycopg.connect(DSN) as db:
        with pytest.raises(TransitionConflict):
            transition_ticket(db,token,ticket,"REPAIRING",1)
        db.execute("UPDATE service_ticket SET state='QC_FAILED' WHERE id=%s",(ticket,))
        with pytest.raises(TransitionConflict):
            transition_ticket(db,token,ticket,"READY_FOR_HANDOVER",1)
        assert transition_ticket(db,token,ticket,"REPAIRING",1)["version"]==2
