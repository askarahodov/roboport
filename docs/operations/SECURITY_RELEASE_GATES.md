# Release blockers (do NOT block synthetic development)

**Internal development allowed now:** synthetic identities, immutable test fixtures, docs/ADR/design, local tests, public code repository with no private data.

**Not allowed without further explicit decision:** public service launch, accepting real orders, storing real PII, collecting payments, sending supplier/customer messages, buying/printing materials, deploying to production.

Mandatory before public beta:
1. Trademark/name/logo similarity, font/image licenses, OEM disclaimers and publication rights.
2. Lawful processing, privacy/retention terms, user consent, role-based PII access and audit by jurisdiction.
3. Real OIDC/PKCE and step-up, user recovery and revoke; no synthetic tokens.
4. Real disposable PG migration / constraints / concurrency tests and restore from backup.
5. Object-level ACL against private documents, TTL, malware guard and upload ACL; S3 backup policy.
6. Full e2e tests, abuse-rate limits, threat model, security review & penetration check.
7. Full responsive accessibility audit, print proof, owner brand sign-off.
8. Supplier compatibility/service capability qualification and safety SOP review for devices/batteries.
9. Revised unit economics including customer portal, brand work, IdP, messaging, infrastructure and support; formal go/no-go.
