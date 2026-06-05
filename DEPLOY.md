# Deploying SemanticOS

SemanticOS is a multi-service stack (FastAPI API + Celery worker + Next.js UI +
Postgres + Redis + ClickHouse + Redpanda + MinIO). Because of the stateful
services and the ML libraries, the **backend must run on a real machine/VM** —
it cannot run on serverless platforms (Vercel, Lambda, etc.).

There are two supported shapes. Pick one.

---

## Option A — Everything on one VM (fastest functional demo)

Best when you just want it live end-to-end with the least moving parts. The UI is
served same-origin behind the bundled nginx, so there are no CORS or cross-origin
TLS concerns.

**Requirements:** a Linux VM with ~4 vCPU / 16 GB RAM / 40 GB disk, Docker +
Docker Compose installed, ports 80/443 open.

```bash
git clone <your-repo> && cd DE-Noiser
./deploy/bootstrap.sh        # generates .env with random secrets, prints the admin login, builds & starts
docker compose ps            # wait until api, web, worker are healthy (first build is slow)
```

Then open `https://<vm-ip>` (the bundled cert is self-signed — accept the browser
warning) and log in as `admin@semanticos.io` with the password the script printed
(also stored in `.env` as `SEMANTICOS_ADMIN_PASSWORD`).

To remove the browser warning, point a domain at the VM and swap the self-signed
cert in `nginx/certs/` for a real one (see "Real TLS" below).

### Free option: Oracle Cloud (OCI) Always Free ARM

OCI's **Ampere A1 (ARM)** Always Free shape gives **4 OCPU / 24 GB RAM free
forever** — enough to run the whole stack at no cost. The entire stack is already
arm64-compatible (the app images build native arm64, and all infra images —
Postgres, Redis, nginx, MinIO, ClickHouse, Redpanda — plus CPU-only torch have
aarch64 builds), so `deploy/bootstrap.sh` works unchanged.

**1. Create the instance**
- Shape: **VM.Standard.A1.Flex**, **4 OCPU / 24 GB RAM** (the ARM "Always Free" shape — *not* the AMD E2.1.Micro, which is too small).
- Image: **Ubuntu 24.04** (aarch64). Boot volume: default (~47 GB) is fine.
- Add your SSH key.
- ⚠️ Free A1 capacity is in high demand — if you get "Out of host capacity",
  try a different Availability Domain or a less busy region and retry.

**2. Open ports — OCI locks them in TWO places (people miss the second):**
- *Cloud firewall:* in the instance's VCN → Security List (or an NSG), add
  ingress rules allowing TCP **80** and **443** from `0.0.0.0/0`.
- *Instance firewall:* OCI's Ubuntu image ships with restrictive iptables that
  block everything but 22. On the box:
  ```bash
  sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80  -j ACCEPT
  sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT
  sudo netfilter-persistent save        # persist across reboots
  ```
  (Do **not** open 8123/9092 — those are bound to localhost by design.)

**3. Install Docker, clone, launch:**
```bash
curl -fsSL https://get.docker.com | sh
git clone https://<YOUR_GH_TOKEN>@github.com/ATULSHARMA1234/DE-Noiser.git
cd DE-Noiser
./deploy/bootstrap.sh        # first build is ~15-20 min on 4 ARM cores (compiles hdbscan)
```

Then open `https://<public-ip>` and log in with the printed admin password.

---

## Option B — Frontend on Vercel + backend on a VM

Best when you want a polished, auto-deploying UI URL. The UI calls the backend
cross-origin, which means the **backend must serve real HTTPS** (an HTTPS page
cannot call an HTTP or self-signed-cert API — browsers block it).

**1. Backend (VM):** same as Option A (`./deploy/bootstrap.sh`), but put real TLS
in front of the API (see "Real TLS"). Note the public API URL, e.g.
`https://api.yourdomain.com`.

**2. Allow the Vercel origin (CORS):** in the VM's `.env`, add your Vercel URL:

```
CORS_ALLOWED_ORIGINS=https://your-app.vercel.app
```

then `docker compose up -d` to apply.

**3. Frontend (Vercel):**
- Import the repo into Vercel and set the **Root Directory** to `web/`.
- Add environment variables:
  - `NEXT_PUBLIC_API_BASE = https://api.yourdomain.com`
  - `NEXT_PUBLIC_WS_BASE  = wss://api.yourdomain.com`
- Deploy. The UI will talk directly to your backend.

---

## Real TLS (for a public domain)

The bundled nginx ships a self-signed `localhost` cert. For a real domain, the
simplest path is to put **Caddy** (automatic Let's Encrypt) or **Traefik** in
front, or replace `nginx/certs/{localhost.crt,localhost.key}` with certs issued
for your domain and set `server_name` in `nginx/nginx.conf`.

---

## What runs where

| Service | Purpose | Notes |
|---|---|---|
| `api` | FastAPI HTTP + WebSocket | `/health` healthcheck |
| `worker` | Celery analysis/SLO worker | **required** — `/analyze` jobs complete here |
| `web` | Next.js UI | only used in Option A (Vercel replaces it in Option B) |
| `nginx` | TLS + reverse proxy | `/` → web, `/api/*` → api, `/stream` → api (WS) |
| `db` / `redis` / `clickhouse` / `redpanda` / `minio` | stateful backends | data in named volumes |

## Operating notes

- **Secrets** live only in `.env` (gitignored). Rotate by editing `.env` and
  re-running `docker compose up -d`.
- **First analysis** downloads the sentence-transformers model into the `worker`
  container; the first `/analyze` is slow, subsequent runs are fast.
- **Backups:** the data lives in Docker named volumes (`postgres_data`,
  `clickhouse_data`, `minio_data`). Snapshot these for DR.
- **Image tags** are currently `:latest` for infra images — pin them before you
  rely on this in production.
