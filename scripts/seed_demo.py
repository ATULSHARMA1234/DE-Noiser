#!/usr/bin/env python3
"""
Seed the SemanticOS backend with representative demo data so every dashboard tab
shows real content (SLOs, metric rules, runbooks, integrations, a dashboard, and
an analysis run).

Usage:
    SEED_PASSWORD='<admin-password>' python3 scripts/seed_demo.py [API_BASE]

  API_BASE defaults to https://localhost/api (the all-in-one nginx deploy).
  For the public box:  python3 scripts/seed_demo.py https://20.2.90.156.nip.io/api
  Admin email defaults to admin@semanticos.io (override with SEED_EMAIL).

Only stdlib is used. TLS verification is disabled so self-signed certs work.
Re-running creates duplicates — run once on a fresh DB.
"""
import json
import os
import ssl
import sys
import urllib.request

API_BASE = (sys.argv[1] if len(sys.argv) > 1 else os.getenv("SEED_API_BASE", "https://localhost/api")).rstrip("/")
EMAIL = os.getenv("SEED_EMAIL", "admin@semanticos.io")
PASSWORD = os.getenv("SEED_PASSWORD")
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE


def call(method, path, token=None, body=None):
    url = f"{API_BASE}{path}"
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, context=CTX, timeout=60) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.load(e)
        except Exception:
            return e.code, {"detail": e.read().decode()[:200]}


def main():
    if not PASSWORD:
        sys.exit("Set SEED_PASSWORD to the admin password (from the backend's .env SEMANTICOS_ADMIN_PASSWORD).")

    print(f"→ Logging in to {API_BASE} as {EMAIL} ...")
    code, res = call("POST", "/auth/login", body={"email": EMAIL, "password": PASSWORD})
    token = res.get("access_token") if code == 200 else None
    if not token:
        sys.exit(f"Login failed ({code}): {res}")
    print("  ✓ authenticated")

    created = {"slos": 0, "metric_rules": 0, "runbooks": 0, "integrations": 0, "dashboards": 0, "runs": 0}

    slos = [
        {"name": "API Availability", "service": "api-gateway", "sli_type": "availability", "target_percentage": 99.9, "window_days": 30},
        {"name": "Checkout Latency", "service": "checkout-service", "sli_type": "latency", "target_percentage": 99.5, "window_days": 30},
        {"name": "Auth Success Rate", "service": "auth-service", "sli_type": "availability", "target_percentage": 99.95, "window_days": 7},
    ]
    for s in slos:
        c, _ = call("POST", "/slos", token, s)
        created["slos"] += c in (200, 201)

    rules = [
        {"name": "Error Rate", "query": "level:ERROR", "aggregation": "count", "window_seconds": 60},
        {"name": "Payment Failures", "query": "service:payment AND failed", "aggregation": "count", "window_seconds": 300},
    ]
    for r in rules:
        c, _ = call("POST", "/metrics/rules", token, r)
        created["metric_rules"] += c in (200, 201)

    runbooks = [
        {"name": "Restart Crashlooping Pod", "trigger_condition": {"keyword": "CrashLoopBackOff"},
         "steps": [{"action": "webhook", "target": "https://hooks.example.com/restart"}, {"action": "escalate", "target": "on-call"}]},
        {"name": "Scale Up On High Latency", "trigger_condition": {"metric": "latency_p99", "gt": 800},
         "steps": [{"action": "restart_service", "target": "checkout-service"}]},
    ]
    for rb in runbooks:
        c, _ = call("POST", "/runbooks", token, rb)
        created["runbooks"] += c in (200, 201)

    integrations = [
        {"provider": "slack", "name": "Ops Slack", "config": {"webhook_url": "https://hooks.slack.com/services/XXX"}},
        {"provider": "pagerduty", "name": "Primary On-Call", "config": {"routing_key": "demo-routing-key"}},
    ]
    for i in integrations:
        c, _ = call("POST", "/integrations", token, i)
        created["integrations"] += c in (200, 201)

    dashboards = [
        {"name": "Reliability Overview", "is_shared": True,
         "widgets": [{"id": "w1", "type": "stat", "title": "Open Incidents", "config": {}},
                     {"id": "w2", "type": "timeseries", "title": "Error Rate", "config": {}}],
         "layout": []},
    ]
    for d in dashboards:
        c, _ = call("POST", "/dashboards", token, d)
        created["dashboards"] += c in (200, 201)

    # Trigger an analysis run so the Runs tab is populated (and Incidents too, if
    # the backend has an LLM key configured via SLD_LLM_API_KEY).
    print("→ Triggering a demo analysis run (this can take ~30s)...")
    c, res = call("POST", "/analyze", token, {"source": "data/demo_baseline.log", "intelligence": False})
    created["runs"] += c in (200, 201, 202)

    print("\n  Seed summary:")
    for k, v in created.items():
        print(f"    {k:14}: {v}")
    print("\n✓ Done. Reload the dashboard — SLOs, Metrics, Runbooks, Integrations and Dashboards tabs are now populated.")
    print("  (Incidents/Alerts/Traces fill in when you run an analysis with an LLM key set, or ingest traces.)")


if __name__ == "__main__":
    main()
