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

## 🚀 Minimal Installation (1-Minute Setup)

The fastest way to get started is using the provided `Makefile`:

```bash
git clone https://github.com/yourusername/semantic-log-denoiser.git
cd semantic-log-denoiser

# This creates a venv, installs dependencies, and sets up your .env file
make install

# Activate the environment
source .venv/bin/activate
```

### Configuration
1. Open the generated `.env` file.
2. Add your `SLD_LLM_API_KEY` (Groq/OpenAI).
3. You're ready to go!

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
