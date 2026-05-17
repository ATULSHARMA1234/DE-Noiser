# 🛡️ Semantic Log De-Noiser

> CLI tool for semantic log clustering, noise reduction, and AI-powered anomaly detection.

Designed for **SREs, Platform Engineers, and DevOps teams** to triage incidents faster by collapsing millions of repetitive log lines into a few meaningful clusters and surfacing hidden anomalies.

---

## ✨ Features

- 🧠 **Semantic Clustering**: Groups logs by meaning using HDBSCAN and Sentence-Transformers (not just regex).
- 🎯 **Anomaly Detection**: Compare logs against a "Known-Good" baseline index to catch novel patterns.
- 🤖 **AI Incident Intelligence**: Automated root-cause analysis and failure domain classification via LLMs (Groq/OpenAI).
- 🔒 **Privacy First**: Local-first processing with automatic PII and secret redaction.
- 🔌 **Cloud Native**: Native adapters for **Kubernetes** (`k8s://`), **AWS CloudWatch** (`aws://`), and **Stdin** (`-`).

---

## 🚀 Quick Start (Enterprise Dashboard)

The project now includes a fully-featured Next.js Enterprise Dashboard.

### Prerequisites
1. **Python 3.10+**
2. **Node.js (v18+)** and `npm`
3. **uv** (Python package manager). Install via: `curl -LsSf https://astral.sh/uv/install.sh | sh`

### 1. Set up the Environment
Create a `.env` file in the root directory:
```env
# Add your AI key for intelligence features (OpenAI / Groq)
LLM_API_KEY="your-api-key-here"
DATABASE_URL="sqlite:///./data/semantic_os.db"
```

### 2. Start the Backend (FastAPI / AI Engine)
```bash
# Install dependencies using uv
uv sync

# Start the server (runs on port 8000)
env PYTHONPATH=src uv run python -m uvicorn src.denoiser.api.main:app --host 0.0.0.0 --port 8000 --reload
```

### 3. Start the Frontend (Next.js)
Open a new terminal window:
```bash
cd web
npm install
npm run dev
```

The dashboard will be available at **`http://localhost:3000/app`**.

---

## 🛠️ Usage

### Analyze Kubernetes Pod Logs
```bash
semantic-log analyze k8s://default/api-pod --intelligence
```

### Catch Anomalies in AWS CloudWatch
```bash
# 1. Build a baseline from a "healthy" time range
semantic-log build-baseline data/healthy-logs.log -o data/prod.index

# 2. Detect anomalies in the current logs
semantic-log analyze aws://log-group/stream --baseline data/prod.index --intelligence
```

### Local File Analysis
```bash
semantic-log analyze ./logs/app-crash.log --top 10
```

---

## 🤝 Contributing
Contributions are welcome! Please see the [Development](#development) section below.

## 📝 License
MIT
