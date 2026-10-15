"""Structural preflight for inherited *draft* OpenAPI; not full OAS validation."""
from pathlib import Path
import yaml

root=Path(__file__).resolve().parents[1]
s=yaml.safe_load((root/'contracts/openapi.yaml').read_text(encoding='utf-8'))
assert s['openapi'].startswith('3.1.'), 'Only OAS 3.1 draft baseline expected'
assert s['info']['title'].startswith('ROBOport')
ops=[(p,method,o) for p,m in s['paths'].items() for method,o in m.items() if method in {'get','put','patch','post','delete'}]
ids=[o['operationId'] for _,_,o in ops]
assert len(ids)==len(set(ids)), 'Duplicate operationId'
assert len(ops)==42, f'Expected 42 inherited draft operations; got {len(ops)}'
assert len(s['components']['schemas'])==56, 'Draft schema count drift; update ADR/tests if intentional'
assert '/api/v1/public/status' in s['paths']
assert '/api/v1/public/status/{token}' not in s['paths']
assert '/api/v1/customer/me' in s['paths']
assert s['components']['securitySchemes']['localSession']['in']=='cookie', 'Expected accepted local session security scheme'
print(f'draft structural preflight OK; {len(ops)} operations, {len(s["components"]["schemas"])} schemas; NOT independent OAS lint')
