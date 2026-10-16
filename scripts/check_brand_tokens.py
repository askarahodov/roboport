"""Deterministic brand handoff guard, stdlib-only.

Checks immutable three-path C industrial symbol, identical JSON/CSS role sets,
and WCAG 2.x AA contrast for real semantic foreground/action/error/safety pairs.
"""
import json
import re
from pathlib import Path
from xml.etree import ElementTree

ROOT=Path(__file__).resolve().parents[1]
TOKENS=ROOT/"design/tokens/theme.json"
STYLES=ROOT/"design/tokens/theme.css"


def channels(value):
    assert re.fullmatch(r"#[0-9a-fA-F]{6}",value), f"invalid color {value}"
    return [int(value[i:i+2],16)/255 for i in (1,3,5)]


def luminance(value):
    red,green,blue=(v/12.92 if v<=0.04045 else ((v+0.055)/1.055)**2.4
                    for v in channels(value))
    return 0.2126*red+0.7152*green+0.0722*blue


def contrast(a,b):
    x,y=sorted([luminance(a),luminance(b)])
    return (y+0.05)/(x+0.05)


def paths(file):
    root=ElementTree.parse(file).getroot()
    return [node.attrib["d"] for node in root.iter() if node.tag.endswith("path")]


def check():
    canonical=paths(ROOT/"design/brand/symbol-light.svg")
    assert len(canonical)==3, "canonical C must contain exactly three paths"
    for item in ("design/brand/symbol-dark.svg",
                 "apps/web/public/logo-light.svg","apps/web/public/logo-dark.svg"):
        assert paths(ROOT/item)==canonical, f"icon drift or substituted symbol: {item}"

    obj=json.loads(TOKENS.read_text(encoding="utf8"))
    assert set(obj)=={"light","dark"}
    assert set(obj["light"])==set(obj["dark"])
    stylesheet=STYLES.read_text(encoding="utf8")
    for name,selector in (("light",r"\.app\s*\{([^}]+)\}"),
                          ("dark",r"\.app\.dark\s*\{([^}]+)\}")):
        match=re.search(selector,stylesheet,re.S)
        assert match, f"missing {name} CSS roles"
        actual={key.removeprefix("--rp-"):value.lower() for key,value in
                re.findall(r"(--rp-[a-zA-Z]+)\s*:\s*(#[a-fA-F0-9]{6})",match.group(1))}
        assert actual=={k:v.lower() for k,v in obj[name].items()},f"JSON/CSS mismatch {name}"
        theme=obj[name]
        for fg,bg in (("foreground","background"),("foreground","surface"),
                      ("actionText","action"),("errorText","errorSurface"),
                      ("safetyText","safetySurface")):
            ratio=contrast(theme[fg],theme[bg])
            assert ratio>=4.5, f"{name} {fg}/{bg}: WCAG ratio {ratio:.2f} < 4.5"
            print(f"{name}: {fg}/{bg} = {ratio:.2f}:1")
        assert contrast(theme["focus"],theme["background"])>=3.0
    print("Canonical C SVG paths and Light/Dark AA roles: PASS")


if __name__=="__main__":
    check()
