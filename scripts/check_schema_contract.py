"""Issue #17: fail when OpenAPI, FSM or executable PostgreSQL enums diverge."""
from pathlib import Path
import ast
import json
import re
import yaml

root=Path(__file__).resolve().parents[1]
api=yaml.safe_load((root/"contracts/openapi.yaml").read_text())
fsm=json.loads((root/"docs/domain/TICKET_TRANSITIONS.json").read_text())
core=(root/"apps/api/alembic/versions/0001_core.py").read_text()
stock=(root/"apps/api/alembic/versions/0002_estimate_stock.py").read_text()

tree=ast.parse(core)
state_node=next(node for node in ast.walk(tree) if isinstance(node,ast.Assign)
                and any(isinstance(t,ast.Name) and t.id=="STATES" for t in node.targets))
sql_states=set(ast.literal_eval(state_node.value))
doc_states=set(fsm["transitions"])
ticket_states=set(api["components"]["schemas"]["Ticket"]["properties"]["state"]["enum"])
transition_states=set(api["components"]["schemas"]["Transition"]["properties"]["targetState"]["enum"])
assert sql_states==doc_states==ticket_states==transition_states, (
    f"Ticket enums drift: SQL={sql_states^doc_states}, API={ticket_states^doc_states}, "
    f"transitions={transition_states^doc_states}")
for origin, targets in fsm["transitions"].items():
    assert all(state in doc_states for state in targets),f"Unknown transition from {origin}"
for terminal in ("SAFETY_HOLD","COMPLETED","DECLINED","CANCELLED",
                 "UNREPAIRABLE","WARRANTY_ROUTE"):
    assert not fsm["transitions"][terminal], f"{terminal} must be guarded/terminal"

match=re.search(r"state IN \(([^)]*)\)",stock)
assert match,"estimate enum constraint not found"
estimate_states=set(re.findall(r"'([A-Z_]+)'",match.group(1)))
api_estimate_states=set(api["components"]["schemas"]["Estimate"]["properties"]["status"]["enum"])
assert estimate_states==api_estimate_states, (
    f"Estimate enum drift: SQL={estimate_states}, OpenAPI={api_estimate_states}")
print(f"schema mapping PASS: {len(sql_states)} ticket states, "
      f"{len(estimate_states)} estimate states, transition matrix checked")
