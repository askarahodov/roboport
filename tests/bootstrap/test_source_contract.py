from pathlib import Path
import json
import yaml
from xml.etree import ElementTree
BASE=Path(__file__).resolve().parents[2]

def test_technical_spec_is_substantive_and_sanitized():
    content=(BASE/'TECHNICAL_SPEC.md').read_text(encoding='utf-8')
    assert len(content)>15000
    for x in ['Idempotency-Key','FastAPI','PostgreSQL','Argon2id','MinIO','RBAC','ACL','CLIENT','SAFETY_HOLD','QC_FAILED','AGENTS.md','C Industrial Lab']:
        assert x in content

def test_contract_draft_inventory_and_public_token_header():
    spec=yaml.safe_load((BASE/'contracts/openapi.yaml').read_text())
    ops=[(path,m,item) for path,entry in spec['paths'].items() for m,item in entry.items() if m in ('get','post','patch','delete','put')]
    assert len(ops)==42
    assert len(spec['components']['schemas'])==56
    assert len({item['operationId'] for _,_,item in ops})==len(ops)
    assert spec['components']['securitySchemes']['localSession']['in']=='cookie'
    assert 'staffBearer' not in spec['components']['securitySchemes']
    assert 'clientBearer' not in spec['components']['securitySchemes']
    assert '/api/v1/public/status' in spec['paths']
    assert not any('{token}' in p.lower() for p in spec['paths'])

def test_design_sources_parse_and_preserve_logo_paths():
    icons=list((BASE/'design/brand').glob('symbol-*.svg'))
    assert icons
    for p in icons:
        rt=ElementTree.fromstring(p.read_text())
        paths=[node for node in rt.iter() if node.tag.endswith('path')]
        assert len(paths)>=3
    tokens=json.loads((BASE/'design/tokens/theme.json').read_text())
    assert tokens


def test_web_uses_original_c_logo_geometry():
    src=(BASE/'apps/web/src/main.tsx').read_text()
    assert 'logo-light.svg' in src and 'logo-dark.svg' in src
    for mode in ('light','dark'):
        canon=(BASE/'design/brand'/f'symbol-{mode}.svg').read_bytes()
        embedded=(BASE/'apps/web/public'/f'logo-{mode}.svg').read_bytes()
        assert embedded==canon
