"""Maintenance CLI must refuse remote DBs and accidental apply."""
import pytest

from scripts.maintain_auto_proof_throttle import local_dev_dsn, main


@pytest.mark.parametrize("dsn", [
    "postgresql://dev:secret@127.0.0.1:5432/roboport",
    "postgresql+psycopg://dev:secret@localhost:5432/roboport",
    "postgresql://dev:secret@[::1]:5432/roboport",
])
def test_only_explicit_loopback_dsn_is_accepted(dsn):
    assert local_dev_dsn(dsn)


@pytest.mark.parametrize("dsn", [
    "",
    "postgresql://dev:secret@db.example.com:5432/prod",
    "postgresql://dev:secret@10.0.0.10:5432/prod",
    "dbname=roboport",
    "postgresql://dev:secret@localhost/db?hostaddr=198.51.100.10",
    "postgresql://dev:secret@localhost/db?service=production",
])
def test_remote_or_implicit_connection_is_refused(dsn):
    assert not local_dev_dsn(dsn)


def test_apply_rejects_missing_synthetic_opt_in(monkeypatch, capsys):
    monkeypatch.setenv("ROBOport_DATABASE_URL",
                       "postgresql://dev:sensitive-password@localhost:5432/roboport")
    monkeypatch.delenv("ROBOport_SYNTHETIC_AUTO_API", raising=False)
    with pytest.raises(SystemExit) as e:
        main(["--apply", "--confirm-synthetic"])
    assert e.value.code == 2
    assert "sensitive-password" not in capsys.readouterr().err


def test_apply_requires_explicit_second_confirmation(monkeypatch):
    monkeypatch.setenv("ROBOport_DATABASE_URL",
                       "postgresql://dev:password@localhost:5432/roboport")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API", "1")
    with pytest.raises(SystemExit) as e:
        main(["--apply"])
    assert e.value.code == 2


@pytest.mark.parametrize("args", [
    ["--batch-size", "0"],
    ["--batch-size", "1001"],
    ["--confirm-synthetic"],
])
def test_bad_cli_args_fail_before_db_connection(args):
    with pytest.raises(SystemExit) as e:
        main(args)
    assert e.value.code == 2
