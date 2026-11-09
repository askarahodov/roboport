"""БоксРитм / Auto Digital: SVG geometry, semantic tokens, WCAG AA and active brand surfaces."""
import json
import re
from pathlib import Path
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[1]
TOKENS = ROOT / "design/tokens/theme.json"
STYLES = ROOT / "design/tokens/theme.css"


def channels(value):
    assert re.fullmatch(r"#[0-9a-fA-F]{6}", value), f"invalid color {value}"
    return [int(value[i:i + 2], 16) / 255 for i in (1, 3, 5)]


def luminance(value):
    red, green, blue = (
        v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
        for v in channels(value)
    )
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast(a, b):
    x, y = sorted([luminance(a), luminance(b)])
    return (y + 0.05) / (x + 0.05)


def paths(file):
    root = ElementTree.parse(file).getroot()
    assert root.tag.endswith("svg"), file
    return [node.attrib["d"] for node in root.iter() if node.tag.endswith("path")]


def check():
    canonical = paths(ROOT / "design/brand/symbol-light.svg")
    assert len(canonical) == 3, "brand must contain garage, car and rhythm paths"
    for item in (
        "design/brand/symbol-dark.svg",
        "design/brand/symbol-mono-black.svg",
        "design/brand/symbol-mono-white.svg",
        "apps/web/public/logo-light.svg",
        "apps/web/public/logo-dark.svg",
        "apps/web/public/favicon.svg",
        "design/brand/favicon.svg",
    ):
        assert paths(ROOT / item) == canonical, f"brand geometry drift: {item}"
    for item in (
        "design/brand/wordmark-light.svg",
        "design/brand/wordmark-dark.svg",
        "design/brand/wordmark-mono.svg",
        "design/brand/brandboard.svg",
    ):
        contents = (ROOT / item).read_text(encoding="utf-8")
        ElementTree.fromstring(contents)
        assert "Бокс" in contents and "Ритм" in contents, item

    obj = json.loads(TOKENS.read_text(encoding="utf-8"))
    assert set(obj) == {"light", "dark"}
    assert set(obj["light"]) == set(obj["dark"])
    stylesheet = STYLES.read_text(encoding="utf-8")
    for name, selector in (
        ("light", r"\.app\s*\{([^}]+)\}"),
        ("dark", r"\.app\.dark\s*\{([^}]+)\}"),
    ):
        match = re.search(selector, stylesheet, re.S)
        assert match, f"missing {name} CSS roles"
        def css_key(key):
            return re.sub(r"-([a-z])", lambda m: m.group(1).upper(), key.removeprefix("--rp-"))
        actual = {
            css_key(key): value.lower()
            for key, value in re.findall(r"(--rp-[a-z-]+)\s*:\s*(#[a-fA-F0-9]{6})", match.group(1))
        }
        assert actual == {k: v.lower() for k, v in obj[name].items()}, f"JSON/CSS mismatch {name}"
        theme = obj[name]
        for fg, bg in (
            ("foreground", "background"), ("foreground", "surface"),
            ("actionText", "action"), ("errorText", "errorSurface"),
            ("safetyText", "safetySurface"), ("successText", "successSurface"),
            ("muted", "background"), ("disabledText", "disabledSurface"),
        ):
            value = contrast(theme[fg], theme[bg])
            assert value >= 4.5, f"{name} {fg}/{bg}: {value:.2f} < WCAG AA 4.5"
        assert contrast(theme["focus"], theme["background"]) >= 3.0
    for item in (
        "apps/web/index.html", "docs/index.html",
        "apps/web/src/main.tsx", "apps/web/src/pages/staff/StaffPortal.tsx",
        "apps/api/roboport_api/main.py", "README.md", "docs/README.md",
    ):
        body = (ROOT / item).read_text(encoding="utf-8")
        assert "БоксРитм" in body, f"missing brand in {item}"
        assert "Airmatik" not in body and "AIRMATIK" not in body, f"retired name in {item}"
    guide = (ROOT / "docs/auto/BRAND_GUIDE.md").read_text(encoding="utf-8")
    for requirement in ("#110", "#119", "Manrope", "legacy", "WCAG", "390/1280/1440"):
        assert requirement in guide
    print("БоксРитм / Auto Digital: canonical SVG, Light/Dark AA, live surfaces PASS")


if __name__ == "__main__":
    check()
