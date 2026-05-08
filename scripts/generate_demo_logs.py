import random
from datetime import datetime, timedelta

def generate_logs(filename, include_incident=False):
    start_time = datetime.now() - timedelta(hours=1)
    logs = []
    
    # Standard noisy clusters
    normal_patterns = [
        "INFO  [UserSession] User {user_id} logged in from {ip}",
        "INFO  [HealthCheck] Service status: OK, CPU: {cpu}%, Mem: {mem}MB",
        "DEBUG [Database] Query executed in {ms}ms: SELECT * FROM products WHERE id={id}",
        "INFO  [API Gateway] 200 OK GET /v1/products/{id}",
        "DEBUG [Cache] Cache hit for key: user_profile_{user_id}",
    ]

    for i in range(150):
        ts = (start_time + timedelta(seconds=i*2)).isoformat() + "Z"
        pattern = random.choice(normal_patterns)
        log = pattern.format(
            user_id=random.randint(1000, 9999),
            ip=f"192.168.1.{random.randint(1, 255)}",
            cpu=random.randint(5, 15),
            mem=random.randint(200, 400),
            ms=random.randint(1, 10),
            id=random.randint(1, 500)
        )
        logs.append(f"{ts} {log}")

    if include_incident:
        # The "Hidden" Incident - Database Timeouts
        incident_ts = (start_time + timedelta(minutes=30)).isoformat() + "Z"
        for _ in range(20):
            logs.append(f"{incident_ts} ERROR [Database] Connection pool exhausted. Timeout after 5000ms. Retrying...")
        
        # The "Fatal" Anomaly - Segfault
        logs.append(f"{incident_ts} FATAL [Kernel] Segmentation fault (core dumped) at 0x00007f8e12b3c4d. Signal 11.")
        logs.append(f"{incident_ts} FATAL [Kernel] Stack trace: #0 0x00007f8e12b3c4d in ?? ()")

    with open(filename, "w") as f:
        f.write("\n".join(logs))

# Generate the files
import os
os.makedirs("data", exist_ok=True)
generate_logs("data/demo_baseline.log", include_incident=False)
generate_logs("data/demo_incident.log", include_incident=True)
print("Created data/demo_baseline.log and data/demo_incident.log")
