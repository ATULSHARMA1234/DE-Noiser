# What and why

<!-- What changes, and what problem it solves. If it fixes a defect, describe
     the defect's effect on a user, not just the code that was wrong. -->

## How this was verified

<!-- Not "tests pass" — CI reports that. What did you run, and what would have
     failed before this change? A fix with no test that fails without it is a
     fix nobody can prove. -->

- [ ] Added or updated a test that fails without this change
- [ ] Ran the full suite locally (`uv run pytest`)

## Checks

- [ ] **Tenant isolation.** Every new query is scoped to a tenant, or this
      touches no customer data. Unscoped reads are the failure mode this
      codebase has hit most.
- [ ] **Migrations.** Schema changes have an Alembic revision, and `alembic
      check` is clean. The down-migration was run, not just written.
- [ ] **API contract.** No field removed, retyped, or newly required in a `/v1`
      response — see the versioning policy in `docs/api.md`. Breaking changes
      go to a new version.
- [ ] **Secrets.** No credential, token or key in code, tests, fixtures or
      logs. New configuration is documented in `.env.example`.
- [ ] **Operations.** New failure modes are observable: a metric, a log line at
      the right level, or an alert rule. Anything that can lose data silently
      needs a series in `deploy/prometheus/alerts.yaml`.
- [ ] **Docs.** `docs/`, `CHANGELOG.md` and the Helm values updated if the
      behaviour, configuration or deployment shape changed.

## Rollout

<!-- Anything that is not "merge and deploy": a migration that must run first,
     a value that must be set before the pods restart, an order between the
     API and the workers, a flag to turn on afterwards. Say "nothing special"
     if that is true. -->
