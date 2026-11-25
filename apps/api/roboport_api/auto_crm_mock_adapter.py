"""Pure offline CRM adapter envelope; no provider, credentials or I/O."""
from hashlib import sha256
from uuid import UUID

ADAPTERS={"CALL":"MOCK_IP_TELEPHONY","EMAIL":"MOCK_EMAIL",
          "SMS":"MOCK_SMS","MESSENGER":"MOCK_MESSENGER"}
TEMPLATES=("BOOKING_REMINDER","SERVICE_UPDATE","POST_SERVICE_REVIEW")


def adapter_receipt(message_id,channel,template):
    """Deterministically validate a fixed mock template without delivery."""
    if type(message_id) is not UUID or channel not in ADAPTERS or template not in TEMPLATES:
        raise ValueError("INVALID_MOCK_ENVELOPE")
    code=ADAPTERS[channel]
    envelope=f"crm-mock-v1|{message_id}|{channel}|{template}|{code}".encode("ascii")
    return {"schemaVersion":1,"adapterCode":code,
            "receiptSha256":sha256(envelope).hexdigest(),
            "networkAllowed":False,"delivered":False}
