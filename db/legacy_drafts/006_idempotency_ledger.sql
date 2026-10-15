-- ROBOport 006 — draft of durable idempotency key registry.
-- NOT executed on PostgreSQL. The FastAPI v0.9 fixture still uses in-memory RLock.
-- Production must atomically tie a successful mutation, an outbox event and response
-- to its idempotency record. Do not assume this table alone achieves exactly-once.
BEGIN;
CREATE TABLE idempotency_request (
  id uuid PRIMARY KEY,
  account_id uuid NOT NULL REFERENCES customer_account(id) ON DELETE RESTRICT,
  key_hash bytea NOT NULL CHECK (octet_length(key_hash) = 32),
  request_hash bytea NOT NULL CHECK (octet_length(request_hash) = 32),
  method text NOT NULL CHECK (method IN ('POST','PUT','PATCH','DELETE')),
  resource_path text NOT NULL CHECK (length(resource_path) > 0),
  status text NOT NULL CHECK (status IN ('IN_PROGRESS','SUCCEEDED')),
  http_status smallint,
  response_json jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  expires_at timestamptz NOT NULL,
  CONSTRAINT idempotency_request_scope_unique UNIQUE (account_id,key_hash),
  CONSTRAINT idempotency_response_consistency CHECK (
    (status = 'IN_PROGRESS' AND http_status IS NULL AND response_json IS NULL) OR
    (status = 'SUCCEEDED' AND http_status BETWEEN 200 AND 299 AND response_json IS NOT NULL)
  ),
  CONSTRAINT idempotency_expiry_gt_creation CHECK (expires_at > created_at)
);
CREATE INDEX idempotency_request_expiry_idx ON idempotency_request(expires_at);
COMMENT ON TABLE idempotency_request IS
  'DRAFT only: reserve account-scoped hashed key transactionally, check request_hash, '
  'serialize concurrent callers, store successful response atomically with side-effects; '
  'never replay without fresh account/resource authorization.';
COMMIT;
