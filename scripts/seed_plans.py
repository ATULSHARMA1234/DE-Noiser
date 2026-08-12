#!/usr/bin/env python3
"""Provision the plan catalogue. This is the act that starts charging.

Entitlement enforcement is off until plan rows exist. That is what lets an
unlicensed self-hosted install keep working exactly as it did instead of losing
SSO, runbooks and tracing behind a 402 it has no way to pay — there is no
payment provider configured to pay *through*.

So this is a script and not a migration. Running the migration must not switch
a customer's features off; running this must be something an operator chose.

    uv run python scripts/seed_plans.py --dry-run     # show what would change
    uv run python scripts/seed_plans.py               # provision
    uv run python scripts/seed_plans.py --grandfather # ...and keep existing
                                                      #    workspaces whole

`--grandfather` gives every existing workspace an `active` subscription on the
plan named by `--grandfather-plan` (default: enterprise). Use it when adding
plans to a deployment that already has customers on it, so nobody loses a
capability they were using this morning. Without it, existing workspaces drop to
the free feature set the moment this runs.

Prices are placeholders. Edit them, and set `provider_price_id` from your
payment provider, before anyone is charged.
"""

from __future__ import annotations

import argparse
import sys

# slug, name, included GB, overage per GB (minor units), base (minor), retention, features
CATALOGUE = (
    ("free", "Free", 5, 0, 0, 7, []),
    ("pro", "Pro", 100, 50, 9_900, 30,
     ["automation", "distributed_tracing", "extended_retention"]),
    ("enterprise", "Enterprise", 1_000, 35, 99_900, 90,
     ["enterprise_identity", "automation", "distributed_tracing", "extended_retention"]),
)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Provision the plan catalogue")
    parser.add_argument("--dry-run", action="store_true", help="show changes, write nothing")
    parser.add_argument(
        "--grandfather", action="store_true",
        help="give existing workspaces a subscription so they lose nothing",
    )
    parser.add_argument("--grandfather-plan", default="enterprise")
    args = parser.parse_args(argv[1:])

    from denoiser.storage.db import Plan, SessionLocal, Subscription, Tenant

    db = SessionLocal()
    try:
        existing = {plan.slug for plan in db.query(Plan).all()}
        if existing and not args.dry_run:
            print(f"Plans already provisioned: {sorted(existing)}")
            print("Edit them in place rather than re-running this.")
            return 0

        print("Plans to create:")
        for slug, name, included, overage, base, retention, features in CATALOGUE:
            money = f"{base / 100:.2f} + {overage / 100:.2f}/GB over {included}GB"
            print(f"  {slug:<12} {name:<12} {money:<28} {retention}d  {features}")

        tenants = db.query(Tenant).all()
        if tenants:
            print(f"\n{len(tenants)} existing workspace(s).")
            if args.grandfather:
                print(
                    f"  --grandfather: each gets an active '{args.grandfather_plan}' "
                    "subscription and keeps every capability."
                )
            else:
                print(
                    "  WITHOUT --grandfather they drop to the free feature set as soon\n"
                    "  as this runs: runbooks, traces and SLOs start answering 402."
                )

        if args.dry_run:
            print("\nDry run — nothing written.")
            return 0

        created = {}
        for slug, name, included, overage, base, retention, features in CATALOGUE:
            plan = Plan(
                slug=slug, name=name, included_gb=included,
                overage_price_minor=overage, base_price_minor=base,
                retention_days=retention, features=features,
                currency="usd", is_public=True,
            )
            db.add(plan)
            created[slug] = plan
        db.commit()

        if args.grandfather:
            target = created.get(args.grandfather_plan)
            if target is None:
                print(f"error: no plan named {args.grandfather_plan!r}", file=sys.stderr)
                return 1
            grandfathered = 0
            for tenant in tenants:
                if db.query(Subscription).filter(Subscription.tenant_id == tenant.id).first():
                    continue
                db.add(Subscription(
                    tenant_id=tenant.id, plan_id=target.id,
                    status="active", provider="manual",
                ))
                grandfathered += 1
            db.commit()
            print(f"Grandfathered {grandfathered} workspace(s) onto '{args.grandfather_plan}'.")

        print(f"\nProvisioned {len(created)} plans. Entitlement enforcement is now ACTIVE.")
        print("Set provider_price_id on each plan before taking a payment.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
