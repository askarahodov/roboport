"""Historical robot draft contract stays archived; browser SDK is retired (#165)."""
from pathlib import Path
import json
import yaml

ROOT=Path(__file__).resolve().parents[2]
SPEC=yaml.safe_load((ROOT/'contracts/openapi.yaml').read_text(encoding='utf-8'))
LOCK=json.loads((ROOT/'contracts/operation_ids.json').read_text(encoding='utf-8'))

def test_archived_robot_contract_operations_are_preserved_for_migration_audit():
    paths=SPEC['paths']
    assert paths['/api/v1/catalog/brands']['get']['operationId']=='listBrands'
    assert paths['/api/v1/catalog/models']['get']['operationId']=='listModels'
    assert paths['/api/v1/catalog/models/{modelId}']['get']['operationId']=='getModel'
    assert len(LOCK['operation_ids'])==46
    # The draft contract is retained as migration evidence, not used by БоксРитм UI.
    assert not (ROOT/"apps/web/src/shared/api/catalogClient.ts").exists()
    main=(ROOT/"apps/web/src/main.tsx").read_text(encoding="utf-8")
    assert "/api/v1/catalog/" not in main
    assert "БоксРитм" in main and "Airmatik" not in main

def test_error_statuses_use_canonical_schema():
    expected={'401','403','404','409','422','429','500'}
    seen=set()
    for operations in SPEC['paths'].values():
        for method,op in operations.items():
            if method not in {'get','post','patch','put','delete'}:continue
            for status,response in op.get('responses',{}).items():
                if status not in expected:continue
                seen.add(status)
                schema=response.get('content',{}).get('application/json',{}).get('schema')
                assert schema=={'$ref':'#/components/schemas/Error'},(op['operationId'],status)
    assert expected<=seen, f'Missing error responses: {expected-seen}'
