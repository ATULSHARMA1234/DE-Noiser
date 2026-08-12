# Deploying SemanticOS

SemanticOS is a multi-service stack (FastAPI API + Celery worker + Next.js UI +
Postgres + Redis + ClickHouse + Apache Kafka + MinIO). Because of the stateful
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
Postgres, Redis, nginx, MinIO, ClickHouse, Kafka — plus CPU-only torch have
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

## Option C — Vercel frontend + free backend via Cloudflare Tunnel (no card, no domain)

When you can't pay for a VM (and a cloud free tier wants a credit card), run the
backend on your own machine and expose it on a free public HTTPS URL with a
**Cloudflare Quick Tunnel** — no Cloudflare account, no domain, no card.

```bash
./deploy/bootstrap.sh     # start the stack (once, if not already running)
./deploy/tunnel.sh        # opens a public https://<random>.trycloudflare.com URL to the API
```

`tunnel.sh` prints the public URL and the exact values to set in Vercel:
- `NEXT_PUBLIC_API_BASE = https://<random>.trycloudflare.com`
- `NEXT_PUBLIC_WS_BASE  = wss://<random>.trycloudflare.com`

Then add your Vercel origin to CORS and apply it:
```bash
# in .env:
CORS_ALLOWED_ORIGINS=https://<your-app>.vercel.app
docker compose up -d api
```

Trade-offs:
- The backend is live **only while your machine + tunnel are running**.
- Quick-tunnel URLs **change on every restart**. For a stable URL, create a
  **named tunnel** with a free Cloudflare account (`cloudflared tunnel login`,
  `cloudflared tunnel create`, route a hostname) — see Cloudflare's docs.

GitHub Codespaces is an alternative free, no-card host: open the repo in a
Codespace, `./deploy/bootstrap.sh`, and make the forwarded port public.

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
| `db` / `redis` / `clickhouse` / `kafka` / `minio` | stateful backends | data in named volumes |

## Operating notes

- **Secrets** live only in `.env` (gitignored). Rotate by editing `.env` and
  re-running `docker compose up -d`.
- **First analysis** downloads the sentence-transformers model into the `worker`
  container; the first `/analyze` is slow, subsequent runs are fast.
- **Backups:** the data lives in Docker named volumes (`postgres_data`,
  `clickhouse_data`, `minio_data`). Snapshot these for DR.
- **Image tags** are currently `:latest` for infra images — pin them before you
  rely on this in production.

---

## Reverse proxy hostname

The `Caddyfile` used to hardcode one specific IP's `nip.io` name, so nobody but
its author could deploy it and TLS issuance depended on a third-party wildcard
DNS service. It now reads two variables:

```bash
export SEMANTICOS_DOMAIN=semanticos.yourcompany.com
export SEMANTICOS_ACME_EMAIL=ops@yourcompany.com
docker compose up -d
```

Port 80 redirects to HTTPS and serves nothing else. Both the proxy and the
application set HSTS, `X-Frame-Options`, `X-Content-Type-Options`,
`Referrer-Policy` and `Permissions-Policy`; the Content-Security-Policy ships in
report-only mode. Once you have reviewed the reports for your deployment, set
`CONTENT_SECURITY_POLICY_ENFORCE=1` to enforce it.

---

## The broker: Apache Kafka by default, Redpanda by choice

The stack ships **Apache Kafka** (`apache/kafka:3.9.0`, Apache-2.0), single-node
KRaft, no ZooKeeper. It used to ship Redpanda, which is BSL 1.1 — permitted to
run yourself, not permitted to offer to third parties as a service. That is a
licensing decision belonging to whoever deploys this, and a default should not
make it for them.

Nothing in the application changed: the broker is spoken to through `aiokafka`,
which is the same protocol either way. Only `KAFKA_BROKER` and the image differ.

To keep Redpanda — it is a single binary, starts faster, and uses less memory:

```bash
docker compose -f docker-compose.yml -f docker-compose.redpanda.yml up -d
```

**Switching an existing install.** The broker is a transit buffer between
`/ingest` and the ingestion worker, not a store of record — anything already
consumed is in ClickHouse. But anything still queued is lost when the broker
changes, so drain before you switch:

```bash
docker compose stop api syslog          # stop producing
docker compose logs -f ingestion        # wait until it stops reporting new batches
docker compose up -d                    # brings up kafka; the old redpanda container is now an orphan
```

Compose will not remove the Redpanda container for you — it is no longer a
service in the base file, so `docker compose rm redpanda` has nothing to act on.
Once you are satisfied the new broker is carrying traffic:

```bash
docker rm -f semanticos-redpanda
docker volume ls | grep redpanda_data   # then `docker volume rm` the one you find
```

Helm installs point `kafka.broker` at whatever broker you already run; the chart
does not deploy one. The default value is `kafka:9092`.

---

## Licensing constraints on the bundled services

SemanticOS is distributed **on-premise**: the customer runs it in their own
infrastructure. Some of the bundled container images are licensed in a way that
depends on that remaining true.

| Component | License | Constraint |
|---|---|---|
| Apache Kafka *(default broker)* | Apache-2.0 | None |
| Redpanda *(opt-in, `docker-compose.redpanda.yml`)* | BSL 1.1 | May not be offered **as a managed service to third parties** |
| Redis 7.4+ | RSALv2 / SSPLv1 | Same shape: using it inside a product is fine, offering Redis itself as a service is not |
| MinIO | AGPL-3.0 | Used unmodified as a container, not linked into SemanticOS. Optional — any S3-compatible endpoint works |

**If SemanticOS is ever offered as a hosted service that you operate**, these
positions expire and must be resolved before launch:

- Stay on the default Apache Kafka broker. Only the opt-in Redpanda override
  carries the BSL restriction, and it is the deployer's deliberate choice.
- Pin `redis:7.2-alpine` (still BSD) or move to Valkey.
- Use the cloud provider's object storage instead of bundling MinIO.

Full detail, including the position on `psycopg2-binary`'s LGPL and the MPL-2.0
components, is in [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md).

---

## The incident narrator sends log content to whatever model you configure

`SLD_LLM_BASE_URL` decides where analysed log content goes. Pointed at a local
model — Ollama, vLLM, anything OpenAI-compatible — nothing leaves your network,
which is the configuration the product is described around.

Pointed at a hosted API, representative log lines from every analysed run are
sent to that provider. The content is redacted first (see
`denoiser.preprocessing.redaction`), but it is still your log data going to a
third party, and that provider becomes a data processor you must name in your
DPA.

The API refuses to start in production with a remote endpoint unless you say you
meant it:

```bash
# Local: nothing to declare.
SLD_LLM_BASE_URL=http://ollama:11434/v1

# Remote: requires an explicit acknowledgement.
SLD_LLM_BASE_URL=https://api.openai.com/v1
LLM_ALLOW_EXTERNAL=true
```

Hostnames without a dot (Compose and Kubernetes service names), `.local`,
`.internal` and `.svc.cluster.local` suffixes, loopback and RFC1918 addresses
are all treated as your own infrastructure and need no acknowledgement.
