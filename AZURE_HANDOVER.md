# Semantic OS — Azure Deployment Handover

This document summarizes the current state of the Semantic OS deployment, cloud infrastructure details, and recent architectural fixes.

## 🚀 Current Status

The application is **fully deployed and operational** on a Microsoft Azure Virtual Machine. Both the Next.js frontend and the FastAPI backend (along with all heavy ML/PyTorch dependencies) are containerized and running inside a single, unified Docker Compose network. 

**Live Application URL:**
[https://20.2.90.156.nip.io](https://20.2.90.156.nip.io)
*(The site is secured with a valid, auto-renewing Let's Encrypt SSL certificate).*

---

## ☁️ Azure Infrastructure Details

The entire stack is running on an Azure VM. 

- **VM Size:** `Standard_E2s_v3` (16 GB RAM)
- **Public IP Address:** `20.2.90.156`
- **Username:** `azureuser`

### 🔑 SSH Access

To access the server terminal, use the following SSH command:
```bash
ssh azureuser@20.2.90.156
```

**Note:** Password authentication is disabled on Azure. You must add the following Public SSH Key to your local machine (or add your own key to the `~/.ssh/authorized_keys` file on the Azure server) to gain access:

```text
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIMpEt8IlAE48zICcA/0GrMPPqMfdInjyWEtgq2zWb1tU admin@semanticos.io
```

---

## 🏗️ Architecture & Docker Stack

All services are orchestrated via Docker Compose (`~/DE-Noiser/docker-compose.yml` on the Azure VM).

* **Reverse Proxy:** Caddy (Ports 80 & 443)
* **Frontend:** Next.js Server (`semanticos-web`)
* **Backend:** FastAPI + AI Workers (`semanticos-api`, `semanticos-worker`)
* **Databases & Queues:**
  * ClickHouse (Log Storage)
  * PostgreSQL (Relational Metadata)
  * Redis (Caching & Celery Broker)
  * Redpanda / Kafka (Event Streaming)
  * MinIO (S3-compatible Object Storage)

---

## 🛠️ Recent Fixes & Changes

To get the deployment fully operational, the following critical changes were made and pushed to the `main` branch:

### 1. SSL & Caddy Configuration Fix
The previous `Caddyfile` contained a bug preventing HTTPS. It was requesting a wildcard certificate (`*.nip.io`) and had `auto_https off` enabled.
* **Fix:** We explicitly set the domain to `20.2.90.156.nip.io`. Let's Encrypt now successfully executes the `http-01` challenge, and the site is securely served over HTTPS without browser warnings.

### 2. Repository Sanitization
The repository was cleaned up to enforce best practices for production builds:
* Deleted irrelevant tracked scratch scripts (`simulate_enterprise.py`, `generate_logs.py`, etc.).
* Removed large binary artifacts (`.docx`, `.pages`) from the Git tree.
* Configured `.gitignore` to universally ignore dynamically generated stream logs (`data/*.log`, `data/*.jsonl`) so they do not clutter Git tracking while the app runs.

---

## 📋 Next Steps for Partner

1. **Verify Access:** SSH into the Azure server using the provided command and key.
2. **Check Logs:** You can view the live traffic logs by running:
   ```bash
   cd ~/DE-Noiser
   docker compose logs -f
   ```
3. **Restarting Services:** If you ever need to reboot the server or apply new code:
   ```bash
   docker compose pull
   docker compose up -d --build
   ```
