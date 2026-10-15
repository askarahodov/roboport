# Modular monolith boundaries, dependency rules

```text
apps/web (public+customer+staff)
    └── REST /api/v1 → API/application adapters
        ├── catalog: Brand/RobotModel/Variant/Dock/Operation
        ├── identity: OIDC Principal/Account/Verified Link
        ├── customers: Customer/Device/Ownership
        ├── tickets: Ticket/Intake/Diagnosis/StateEvent
        ├── estimates: Estimate/Line/Approval Version
        ├── stock: Part/Compatibility/Lot/Reservation/Movement
        ├── quality: Worklog/QC/Handover
        ├── documents: Attachment/AccessAudit/Quarantine
        ├── notification: Outbox/Preferences/Deliveries
        └── reporting/audit: append-only events, read projections
                  ├── PostgreSQL 17 (single transactional DB)
                  └── external OIDC & private S3
```

Each bounded context owns its tables and migrations; cross-module relations are service interfaces and validated repository queries, never arbitrary ORM import cycles. Mutations go through use-cases with policy checks and DB transactions. All staff/customer access requires ABAC at service layer. UI never receives fields marked INTERNAL/SECRET/PII without role & object authorization. Avoid Kafka, Elasticsearch, microservices, RKE2 deployment templates until a measured need. No inventing alternate service roles or YAML charts during Sprint 0.

**Dependency direction:** HTTP schemas/adapters → usecases → domain model; repository adapters implement domain ports. Domain layer does not import FastAPI, ORM, cloud provider. All clock/UUID services injected for deterministic tests. Store event history immutable with actor, ticket, previous/next state and request correlation. Transactions own stock reservation, estimate approval and outbox rows. Strict DB scoped connections; no shared mutable production state.
