"""SQL-negative tests for draft estimate immutability and stock bounds (real PostgreSQL)."""
from uuid import uuid4
import psycopg
import pytest
from test_pg_baseline import DSN


def test_sent_estimate_lines_and_header_are_frozen():
    with psycopg.connect(DSN) as conn:
        customer, ticket, estimate, line = (uuid4() for _ in range(4))
        conn.execute("INSERT INTO customer(id,label) VALUES (%s,'estimate-owner')", (customer,))
        conn.execute("INSERT INTO service_ticket(id,customer_id) VALUES (%s,%s)", (ticket, customer))
        conn.execute(
            "INSERT INTO estimate(id,ticket_id,version,total_minor,currency) VALUES (%s,%s,1,1000,'RUB')",
            (estimate,ticket),
        )
        conn.execute(
            "INSERT INTO estimate_line(id,estimate_id,description,amount_minor) VALUES (%s,%s,'diagnosis',1000)",
            (line,estimate),
        )
        conn.execute("UPDATE estimate SET state='SENT' WHERE id=%s",(estimate,))
        for statement,params in [
            ("UPDATE estimate_line SET amount_minor=900 WHERE id=%s",(line,)),
            ("DELETE FROM estimate_line WHERE id=%s",(line,)),
            ("INSERT INTO estimate_line(id,estimate_id,description,amount_minor) VALUES (%s,%s,'hidden',100)",(uuid4(),estimate)),
            ("UPDATE estimate SET total_minor=900 WHERE id=%s",(estimate,)),
        ]:
            with pytest.raises(psycopg.errors.RaiseException):
                with conn.transaction():
                    conn.execute(statement,params)


def test_stock_bounds_and_estimate_nonnegative_amounts():
    with psycopg.connect(DSN) as conn:
        lot=uuid4()
        conn.execute("INSERT INTO stock_lot(id,part_number,available_qty,reserved_qty) VALUES (%s,'synthetic-pn',2,1)",(lot,))
        for sql in [
            "UPDATE stock_lot SET available_qty=-1 WHERE id=%s",
            "UPDATE stock_lot SET reserved_qty=3 WHERE id=%s",
            "UPDATE stock_lot SET reserved_qty=-1 WHERE id=%s",
        ]:
            with pytest.raises(psycopg.errors.CheckViolation):
                with conn.transaction():
                    conn.execute(sql,(lot,))
        customer,ticket,estimate=uuid4(),uuid4(),uuid4()
        conn.execute("INSERT INTO customer(id,label) VALUES (%s,'total-owner')",(customer,))
        conn.execute("INSERT INTO service_ticket(id,customer_id) VALUES (%s,%s)",(ticket,customer))
        with pytest.raises(psycopg.errors.CheckViolation):
            with conn.transaction():
                conn.execute("INSERT INTO estimate(id,ticket_id,version,total_minor,currency) VALUES (%s,%s,1,-1,'RUB')",(estimate,ticket))
