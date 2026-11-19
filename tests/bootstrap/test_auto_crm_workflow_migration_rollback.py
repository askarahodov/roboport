from pathlib import Path


def _guard_body(source: str) -> str:
    marker = "CREATE OR REPLACE FUNCTION guard_auto_crm_followup_task()"
    start = source.index(marker)
    end = source.index('    """)', start)
    return source[start:end].strip()


def test_0132_downgrade_restores_full_0100_post_service_review_guard():
    root = Path(__file__).resolve().parents[2]
    migrations = root / "apps" / "api" / "alembic" / "versions"
    original = (migrations / "0100_auto_crm_post_service_review.py").read_text(encoding="utf-8")
    current = (migrations / "0132_auto_crm_workflow.py").read_text(encoding="utf-8")
    downgrade = current.split("def downgrade()", 1)[1]

    assert _guard_body(downgrade) == _guard_body(original)
    restored = _guard_body(downgrade)
    assert "NEW.origin_work_order_id" in restored
    assert "SERVICE_REVIEW" in restored
    assert "auto_work_order_execution_finish" in restored
    assert "auto_work_order_qc_event" in restored
    assert "qc.result='PASS'" in restored
