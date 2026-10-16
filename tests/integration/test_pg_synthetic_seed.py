"""Dev seed executes against disposable PostgreSQL, never production."""
import sys
from uuid import NAMESPACE_URL,uuid5
import psycopg
from test_pg_baseline import DSN
from roboport_api.passwords import verify_password


def test_seed_three_synthetic_accounts_without_cleartext(monkeypatch,capsys):
    from scripts.seed_synthetic_users import main
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API","1")
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    names=["CLIENT_A","CLIENT_B","ADMIN"]
    for key in names:
        monkeypatch.setenv(f"ROBOport_SEED_{key}_PASSWORD","Synthetic-Only-"+key+"-Safe!")
    monkeypatch.setattr(sys,"argv",["seed","--i-understand-dev-only"])
    main()
    main()
    text=capsys.readouterr().out
    assert "Synthetic-Only" not in text
    with psycopg.connect(DSN) as conn:
        for key,login in zip(names,["synthetic-customer-a","synthetic-customer-b","synthetic-staff-admin"]):
            uid=uuid5(NAMESPACE_URL,"roboport-dev-only://"+login)
            result=conn.execute("""SELECT c.password_hash FROM app_user u
               JOIN local_credential c ON c.user_id=u.id WHERE u.id=%s""",(uid,)).fetchone()
            assert result and result[0].startswith("$argon2id$")
            assert verify_password("Synthetic-Only-"+key+"-Safe!",result[0])
            assert conn.execute("SELECT count(*) FROM user_role WHERE user_id=%s",
                                (uid,)).fetchone()[0]==1
    assert all(pwd not in text for pwd in ["Synthetic-Only-"+x+"-Safe!" for x in names])


def test_seed_requires_explicit_opt_in_and_secrets(monkeypatch):
    import pytest
    from scripts.seed_synthetic_users import main
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    monkeypatch.delenv("ROBOport_SYNTHETIC_AUTH_API",raising=False)
    monkeypatch.setattr(sys,"argv",["seed","--i-understand-dev-only"])
    with pytest.raises(SystemExit):
        main()
