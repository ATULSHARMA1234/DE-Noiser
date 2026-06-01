# Enterprise Scale Simulation Report

**Date:** 2026-05-31T00:17:20.810403

This report documents a simulated enterprise workflow executing the core capabilities of the Semantic Log Denoiser platform.

## 1. System Health Check
- **Endpoint**: `GET /health`
- **Response Time**: 0.017s
- **Output**: `{"status":"healthy","version":"2.0.0"}`

## 1.5 Authentication
- **Output**: Authenticated successfully. Token acquired.

## 2. Mass Log Ingestion
Simulating a payment service experiencing a database connection spike and intermittent 500 errors.
- **Input Size**: 120 logs sent.
- **Endpoint**: `POST /ingest`
- **Response Time**: 0.025s
- **Output**: `{"status":"success","ingested":120}`

*Waiting 2 seconds for logs to be indexed...*

## 3. Semantic Log Query (LQL)
- **Input Query**: `service:payment-api AND level:ERROR`
- **Endpoint**: `POST /v1/logs/query`
- **Response Time**: 0.015s
- **Output**: Successfully retrieved 0 matching logs.

## 4. Configuring Alert Destinations
- **Input**: Created webhook `PagerDuty Escalation` routing to `https://events.pagerduty.com/integration/demo/enqueue`
- **Endpoint**: `POST /webhooks`
- **Response Time**: 0.010s
- **Output**: 201 - `{"status":"registered","id":"7f1c3e7a","name":"PagerDuty Escalation","channel_type":"pagerduty","url":"https://events.pagerduty.com/integration/demo/enqueue","min_priority":"P1","enabled":true,"extra":{}}`

## 5. AI Inference (Incident Intelligence)
Running the LLM-based analysis engine to cluster logs and generate intelligence reports.
- **Input**: `{'source': 'data/live_stream.log', 'intelligence': True, 'top_n': 5}`
- **Endpoint**: `POST /analyze` -> returned Task ID `aceb5a7a-e3c2-4f4d-bb09-589ccb584bc9`
- **Response Time**: 25.318s
- **Output**: `{'task_id': 'aceb5a7a-e3c2-4f4d-bb09-589ccb584bc9', 'status': 'SUCCESS', 'result': {'status': 'success', 'run_id': 'aceb5a7a-e3c2-4f4d-bb09-589ccb584bc9', 'clusters': [{'id': 2, 'cluster_id': 2, 'size': 2674, 'summary': 'Containerd-shim process start', 'source': '/Users/atul/Desktop/semantic-log-denoiser(Esha-Atul)/data/live_stream.log:1332', 'representative_log': '{"@timestamp": "2026-05-29T18:38:38.901913804Z", "event": {"kind": "event", "category": "process", "type": "start", "dataset": "ebpf.process", "action": "execve"}, "host": {"hostname": "semanticos-node", "os": {"platform": "linux", "family": "alpine"}}, "process": {"pid": 21614, "name": "containerd-shim", "executable": "/usr/bin/runc"}}', 'representative_template': '{"@timestamp": "<TIMESTAMP>", "event": {"kind": "event", "category": "process", "type": "start", "dataset": "ebpf.process", "action": "execve"}, "host": {"hostname": "semanticos-node", "os": {"platform": "linux", "family": "alpine"}}, "process": {"pid": <NUMBER>,...`
