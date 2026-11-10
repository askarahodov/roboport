"""Disposable OIDC Keycloak test fixture, DO NOT run against real data."""
from uuid import UUID
import psycopg
from test_pg_baseline import DSN

issuer="https://localhost:9443/realms/roboport-demo"
reviewer="99999999-9999-4999-8999-999999999999"
fixtures=[
    ("11111111-1111-4111-8111-111111111111","aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
     "cccccccc-cccc-4ccc-8ccc-cccccccccccc","dddddddd-dddd-4ddd-8ddd-dddddddddddd"),
    ("22222222-2222-4222-8222-222222222222","bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
     "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee","ffffffff-ffff-4fff-8fff-ffffffffffff"),
]
with psycopg.connect(DSN) as db:
    db.execute("INSERT INTO app_user(id,normalized_login) VALUES(%s,%s)",
               (reviewer,"synthetic-independent-reviewer"))
    for sub,user,customer,vehicle in fixtures:
        db.execute("INSERT INTO app_user(id,normalized_login) VALUES(%s,%s)",(user,"synthetic-oidc-"+sub[:8]))
        db.execute("INSERT INTO user_role(id,user_id,role_code) VALUES(gen_random_uuid(),%s,'CLIENT')",(user,))
        db.execute("INSERT INTO customer(id,label) VALUES(%s,%s)",(customer,"synthetic-"+sub[:8]))
        db.execute(
            "INSERT INTO auto_vehicle(id,make,model,vin,model_year) "
            "VALUES(%s,'SyntheticMake','DemoCar',%s,2026)",
            (vehicle,"TEST"+sub.split("-")[0]+"00000"),
        )
        db.execute("INSERT INTO account_customer_link(user_id,customer_id) VALUES(%s,%s)",(user,customer))
        db.execute(
            "INSERT INTO auto_vehicle_customer_link(id,vehicle_id,customer_id,verified_at,verified_by) "
            "VALUES(gen_random_uuid(),%s,%s,now(),%s)",
            (vehicle,customer,reviewer),
        )
        db.execute("INSERT INTO oidc_identity(issuer,subject,user_id) VALUES(%s,%s,%s)",(issuer,sub,user))
print("Synthetic IdP/PG automotive ownership A/B links prepared")
