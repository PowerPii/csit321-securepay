# Working on SecurePay

## Branches and pull requests

- Two long-lived branches: `main` (production, v1.0.0, the live server) and `dev` (where v2 work lands; the staging server at staging.securepaysg.me runs it). Branch from `dev` for v2 work and from `main` only for a v1 fix. The whole flow is in [BRANCHING.md](BRANCHING.md).
- `main` is always green. Nobody pushes to it directly; everything goes through a pull request.
- Branch names: `feat/<area>-<short-name>`, `fix/<area>-<short-name>`, `docs/<short-name>`, for example `feat/payments-idempotency`.
- One pull request per piece of work, small enough to review in 20 minutes. Link the requirement IDs it covers (for example "CP FR 9") and the demo story it moves forward (S1 to S6 in `docs/MVP-SCOPE.md`).
- At least one teammate reviews before merging. CI must pass.

## Rules reviewers check

1. No card number, CVV or secret outside `gateway/apps/vault` (logs, audit, outbox, features, fixtures). Add a test when you touch these paths.
2. Every merchant or staff query is scoped to the caller's merchant or role. Add a negative test that another merchant gets 404.
3. Every payment state change goes through `apps/payments/states.check_transition` and writes the outbox row in the same transaction.
4. State-changing merchant API calls require an `Idempotency-Key`.
5. Business numbers come from `settings.GATEWAY_RULES`, never hard-coded.
6. Do not import gateway code into the simulator or merchant, or the other way round. Share only `contracts/` and `research/features/`.

## Commit messages

Short imperative subject (`Add idempotency table`), then a blank line and why, if it is not obvious.
