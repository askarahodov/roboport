"""Alembic version_num в PostgreSQL имеет стандартную ширину 32."""
import ast
from pathlib import Path


def test_all_revision_ids_fit_alembic_version_column():
    versions = Path(__file__).resolve().parents[2] / "apps/api/alembic/versions"
    files = sorted(versions.glob("*.py"))
    assert files, "migration directory is empty"
    for path in files:
        if path.name == "__init__.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        revisions = [
            node.value for node in tree.body
            if (isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id == "revision"
                        for target in node.targets))
            or (isinstance(node, ast.AnnAssign)
                and isinstance(node.target, ast.Name) and node.target.id == "revision")
        ]
        assert len(revisions) == 1, path.name
        revision = ast.literal_eval(revisions[0])
        assert isinstance(revision, str) and 0 < len(revision) <= 32, (path.name, revision)
