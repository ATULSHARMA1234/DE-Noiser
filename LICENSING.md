# Licensing

SemanticOS is **source-available** under the [Elastic License 2.0](LICENSE).

The source stays public and readable. That is deliberate and not going to change:
the product's central claim is that your log data never leaves your
infrastructure, and the only honest way to back that claim is to let your
security team read the code that handles it. A binary you cannot inspect is a
promise; source you can audit is evidence.

What changed is that reading it no longer comes with permission to resell it.

## Why this isn't MIT any more

Up to and including commit `97ad799`, SemanticOS was MIT-licensed. MIT grants the
right to modify, redistribute, and sell the software — including the right to
delete the parts that decide who has paid, and the right for a cloud provider to
offer SemanticOS as a competing managed service.

That is not a hypothetical. Elastic moved off Apache 2.0 in 2021 after AWS
resold Elasticsearch; HashiCorp moved to BSL in 2023; Redis in 2024. Every one of
those was a company discovering that a permissive licence and a commercial
product do not coexist.

ELv2 keeps everything that made the open licence valuable — public source,
self-hosting, modification, production use — and removes only the three things
that make a business impossible.

## What you may do

- **Read all of it.** Audit it, run static analysis over it, hand it to your
  security team.
- **Run it in production**, on your own infrastructure, for your own
  organisation, at any scale, including commercially.
- **Modify it.** Patch it, extend it, integrate it, fix bugs.
- **Redistribute it**, provided the licence and notices travel with it and
  modified copies say prominently that they were modified.
- **Contribute** fixes and features back.

No licence key is required for any of this. There is no phone-home, and there
never will be one that is required to boot.

## What you may not do

1. **Offer it to third parties as a hosted or managed service** where they get a
   substantial set of its features. Running it for your own organisation — every
   team, every subsidiary — is fine. Selling access to strangers is not.
2. **Circumvent the licence key functionality**, or remove or obscure the
   features it protects.
3. **Remove or obscure licensing, copyright, or other notices.**

## Free and paid

The core product is free and always will be. The commercial tier is the
enterprise surface, defined in
[`entitlements.py`](src/denoiser/api/entitlements.py):

| Capability | Tier |
|---|---|
| Semantic clustering, issue tracking, causal RCA | Free |
| Log ingest — syslog, HTTP, OTLP, Kafka, agent | Free |
| The Command Center UI, query DSL, dashboards | Free |
| Local LLM incident narratives | Free |
| Enterprise identity — OIDC/SAML SSO, SCIM (`enterprise_identity`) | Paid |
| Runbook execution and alert routing (`automation`) | Paid |
| OTLP trace ingest and the trace explorer (`distributed_tracing`) | Paid |
| Extended retention, SLO tracking and forecasting (`extended_retention`) | Paid |

An unlicensed deployment analyses logs — the core product — indefinitely.

**A lapsed licence never causes an outage.** Ingestion and alerting keep running;
only the paid capabilities gate off. Cutting off someone's observability platform
the hour their card expires is how a billing problem becomes an incident, and the
code is written to make sure that cannot happen.

## Air-gapped deployments

Licence validation is an offline signature check against a public key baked into
the image. It requires no outbound network access, ever. Air-gapped and
classified environments are a supported, tested configuration, not an
afterthought.

## The MIT boundary

Relicensing is **not retroactive**. Every commit up to and including `97ad799`
was published under MIT and stays available under MIT permanently. Anyone who
obtained a copy under those terms keeps those rights for that copy, including the
right to fork and continue independently.

ELv2 applies to everything after `97ad799`.

## The agent

> **Open decision.** [`agent/`](agent/) is a separate Go module that runs on
> customer machines and reads their data. Datadog open-sources its Agent (Apache
> 2.0) for exactly that reason: a collector you install on your own hosts should
> be one you can audit. Releasing `agent/` under Apache 2.0 while the platform
> stays ELv2 is the recommended split, and would need its own `agent/LICENSE`.
> Not done yet — decide before the next release.

## Contributions

By contributing you agree your contribution is licensed under ELv2. If you have
contributed previously under MIT, that work remains MIT-licensed in the commits
where it was published.

## Third-party dependencies

This change affects SemanticOS's own source only. Bundled and depended-on
components keep their own licences, and the ELv2 change introduces no new
conflicts — see [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md) for the full
inventory, including the constraints that Redpanda (BSL 1.1), Redis (RSALv2) and
MinIO (AGPL-3.0) place on a hosted offering.

## Commercial licensing

For a commercial licence, a hosted-service arrangement, or anything the terms
above do not permit, get in touch — these are negotiable, and the licence exists
to start that conversation rather than end it.

---

*This document explains the licence in plain terms; the [LICENSE](LICENSE) file
is what governs. Neither is legal advice — have a lawyer review both, and your
customer contracts, before taking payment.*
