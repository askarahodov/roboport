"""CI gate: ensure all installed project runtime/test requirements are pinned."""
from importlib import metadata
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[1]
requirements = ROOT / "apps/api/requirements-dev.txt"
constraints = ROOT / "apps/api/constraints-py313.txt"


def pins(path: Path):
    result = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        req = Requirement(line)
        if str(req.specifier).count("==") != 1 or "," in str(req.specifier):
            raise ValueError(f"must pin exact version: {line}")
        result[canonicalize_name(req.name)] = str(req.specifier).split("==")[1]
    return result


def main():
    specified = pins(requirements)
    locked = pins(constraints)
    missing = []
    pending = []
    for line in requirements.read_text().splitlines():
        if line.strip() and not line.startswith("#"):
            req = Requirement(line.strip())
            pending.append((canonicalize_name(req.name), tuple(sorted(req.extras))))
    seen = set()
    while pending:
        name, extras = pending.pop()
        if (name, extras) in seen:
            continue
        seen.add((name, extras))
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            missing.append(f"{name}: not installed")
            continue
        pinned = locked.get(name)
        if pinned is None or dist.version != pinned:
            missing.append(f"{name}: installed {dist.version}, constraint {pinned}")
        for spec in dist.requires or ():
            dep = Requirement(spec)
            active = (None,) + extras
            if dep.marker is not None and not any(
                dep.marker.evaluate({"extra": e or ""}) for e in active
            ):
                continue
            pending.append((canonicalize_name(dep.name), tuple(sorted(dep.extras))))
    if missing:
        raise SystemExit("Python pin gate FAILED:\n" + "\n".join(sorted(set(missing))))
    print(f"Python pin gate PASS: {len(set(n for n, _ in seen))} direct+transitive distributions")


if __name__ == "__main__":
    main()
