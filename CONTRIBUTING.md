# Contributing to SemanticOS

First off, thank you for considering contributing to SemanticOS! It's people like you that make SemanticOS such a great observability platform.

## Code of Conduct

By participating in this project, you are expected to uphold our Code of Conduct. Please be respectful, inclusive, and professional in all interactions.

## How to Contribute

### 1. Reporting Bugs
- Check the issue tracker to see if the bug has already been reported.
- If not, open a new issue. Please include:
  - A clear, descriptive title.
  - Steps to reproduce the bug.
  - Expected vs. actual behavior.
  - Your environment details (OS, Python version, Docker version, etc.).

### 2. Suggesting Enhancements
- Open an issue describing the feature.
- Explain *why* this enhancement would be useful to most users.
- If you have an idea of how to implement it, please include it!

### 3. Pull Requests
1. **Fork the repo** and create your branch from `main`.
2. **Install dependencies**:
   - Backend: `uv sync`
   - Frontend: `cd web && npm install`
3. **Make your changes**. If you've added code that should be tested, add tests.
4. **Ensure the test suite passes**.
5. **Update documentation** if you've changed APIs or features.
6. **Submit that pull request!**

## Development Setup

### Backend (Python)
We use `uv` for lightning-fast dependency management.
```bash
uv sync
uv run python -m uvicorn denoiser.api.main:app --reload
```

### Frontend (Next.js)
```bash
cd web
npm run dev
```

### eBPF Agent (C/Go)
If modifying the agent, you will need a Linux machine (or VM/container) with `clang` and `llvm` installed.
```bash
cd agent
make generate
make build
```

## Architecture

Please review `README.md` for a high-level overview. When adding new features, try to respect the existing boundaries between the ingestion gateway (`api`), background workers (`analysis_worker.py`), and storage interfaces (`clickhouse_store.py`).
