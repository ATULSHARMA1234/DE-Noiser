import requests
import json
import time
import datetime
import os

BASE_URL = "http://localhost:8000"

def run_simulation():
    report = []
    report.append("# Enterprise Scale Simulation Report\n")
    report.append(f"**Date:** {datetime.datetime.now().isoformat()}\n")
    report.append("This report documents a simulated enterprise workflow executing the core capabilities of the Semantic Log Denoiser platform.\n")

    # 1. Health Check
    report.append("## 1. System Health Check")
    start = time.time()
    res = requests.get(f"{BASE_URL}/health")
    elapsed = time.time() - start
    report.append(f"- **Endpoint**: `GET /health`")
    report.append(f"- **Response Time**: {elapsed:.3f}s")
    report.append(f"- **Output**: `{res.text}`\n")

    # 1.5 Authenticate
    report.append("## 1.5 Authentication")
    login_res = requests.post(f"{BASE_URL}/auth/login", json={"email": "admin@semanticos.io", "password": "admin123"})
    token = login_res.json().get("access_token") if login_res.status_code == 200 else ""
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    if token:
        report.append(f"- **Output**: Authenticated successfully. Token acquired.\n")
    else:
        report.append(f"- **Error**: Failed to authenticate. {login_res.status_code} - {login_res.text}\n")

    # 2. Ingest Logs
    report.append("## 2. Mass Log Ingestion")
    report.append("Simulating a payment service experiencing a database connection spike and intermittent 500 errors.")
    
    logs = []
    # Generate 100 normal logs
    for i in range(100):
        logs.append({"timestamp": datetime.datetime.now().isoformat(), "service": "payment-api", "level": "INFO", "message": f"Processed payment transaction TXN-{1000+i} successfully."})
    # Generate 20 error logs
    for i in range(20):
        logs.append({"timestamp": datetime.datetime.now().isoformat(), "service": "payment-api", "level": "ERROR", "message": f"DatabaseConnectionError: timeout waiting for connection from pool (retry {i%3})"})
    
    payload = {"logs": logs}
    report.append(f"- **Input Size**: {len(logs)} logs sent.")
    
    start = time.time()
    try:
        res = requests.post(f"{BASE_URL}/ingest", json=payload, headers=headers)
        elapsed = time.time() - start
        report.append(f"- **Endpoint**: `POST /ingest`")
        report.append(f"- **Response Time**: {elapsed:.3f}s")
        report.append(f"- **Output**: `{res.text}`\n")
        
        # Wait for ClickHouse async flush
        report.append("*Waiting 2 seconds for logs to be indexed...*\n")
        time.sleep(2)
    except Exception as e:
        report.append(f"- **Error**: {e}\n")

    # 3. Log Search (LQL)
    report.append("## 3. Semantic Log Query (LQL)")
    query_payload = {"query": "service:payment-api AND level:ERROR", "limit": 10}
    report.append(f"- **Input Query**: `{query_payload['query']}`")
    start = time.time()
    try:
        # Use headers established above
        res = requests.post(f"{BASE_URL}/v1/logs/query", json=query_payload, headers=headers)
        elapsed = time.time() - start
        report.append(f"- **Endpoint**: `POST /v1/logs/query`")
        report.append(f"- **Response Time**: {elapsed:.3f}s")
        if res.status_code == 200:
            count = res.json().get('count', 0)
            report.append(f"- **Output**: Successfully retrieved {count} matching logs.\n")
        else:
            report.append(f"- **Output**: {res.status_code} - {res.text}\n")
    except Exception as e:
        report.append(f"- **Error**: {e}\n")

    # 4. Webhook Configuration (Alerting)
    report.append("## 4. Configuring Alert Destinations")
    webhook_payload = {
        "name": "PagerDuty Escalation",
        "channel_type": "pagerduty",
        "url": "https://events.pagerduty.com/integration/demo/enqueue",
        "min_priority": "P1",
        "enabled": True
    }
    report.append(f"- **Input**: Created webhook `{webhook_payload['name']}` routing to `{webhook_payload['url']}`")
    start = time.time()
    try:
        res = requests.post(f"{BASE_URL}/webhooks", json=webhook_payload, headers=headers)
        elapsed = time.time() - start
        report.append(f"- **Endpoint**: `POST /webhooks`")
        report.append(f"- **Response Time**: {elapsed:.3f}s")
        report.append(f"- **Output**: {res.status_code} - `{res.text}`\n")
    except Exception as e:
         report.append(f"- **Error**: {e}\n")

    # 5. AI Inference
    report.append("## 5. AI Inference (Incident Intelligence)")
    report.append("Running the LLM-based analysis engine to cluster logs and generate intelligence reports.")
    analyze_payload = {
        "source": "data/live_stream.log",
        "intelligence": True,
        "top_n": 5
    }
    report.append(f"- **Input**: `{analyze_payload}`")
    start = time.time()
    try:
        res = requests.post(f"{BASE_URL}/analyze", json=analyze_payload, headers=headers)
        if res.status_code == 200:
            data = res.json()
            if data.get("status") == "queued":
                task_id = data.get("task_id")
                report.append(f"- **Endpoint**: `POST /analyze` -> returned Task ID `{task_id}`")
                
                # Poll task
                for _ in range(30):
                    time.sleep(2)
                    task_res = requests.get(f"{BASE_URL}/tasks/{task_id}", headers=headers).json()
                    if task_res.get("status") in ["SUCCESS", "FAILURE"]:
                        elapsed = time.time() - start
                        report.append(f"- **Response Time**: {elapsed:.3f}s")
                        # Truncate large result for report
                        result_str = str(task_res)[:1000] + "..." if len(str(task_res)) > 1000 else str(task_res)
                        report.append(f"- **Output**: `{result_str}`\n")
                        break
            else:
                elapsed = time.time() - start
                report.append(f"- **Endpoint**: `POST /analyze` (Synchronous Fallback)")
                report.append(f"- **Response Time**: {elapsed:.3f}s")
                result_str = str(data)[:1000] + "..." if len(str(data)) > 1000 else str(data)
                report.append(f"- **Output**: `{result_str}`\n")
        else:
            elapsed = time.time() - start
            report.append(f"- **Endpoint**: `POST /analyze`")
            report.append(f"- **Response Time**: {elapsed:.3f}s")
            report.append(f"- **Output**: {res.status_code} - `{res.text}`\n")
    except Exception as e:
         report.append(f"- **Error**: {e}\n")

    # Write report
    with open("enterprise_simulation_report.md", "w") as f:
        f.write("\n".join(report))
    
    print("Simulation complete. Report generated at enterprise_simulation_report.md")

if __name__ == "__main__":
    run_simulation()
