# Contributing to SemanticOS

Thank you for your interest in contributing to SemanticOS! We welcome contributions of all forms, including bug fixes, feature requests, documentation improvements, and architectural suggestions.

To ensure a smooth collaboration, please follow the guidelines documented below.

---

## 🛠️ Development Setup Guide

### Backend Environment Setup
SemanticOS leverages `uv` as its primary Python package and workspace manager.

1. **Install uv**:
   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```

2. **Clone the Repository & Install Dependencies**:
   ```bash
   git clone https://github.com/semanticos/semantic-log-denoiser.git
   cd semantic-log-denoiser
   uv sync
   ```

3. **Configure Environment Settings**:
   Copy or create a `.env` file in the root directory:
   ```env
   DATABASE_URL="sqlite:///./data/semantic_os.db"
   LLM_API_KEY="your-api-key-here"
   REDIS_URL="redis://localhost:6379/0"
   ```

4. **Run FastAPI Server**:
   ```bash
   PYTHONPATH=src uv run python -m uvicorn denoiser.api.main:app --host 0.0.0.0 --port 8000 --reload
   ```

5. **Run Celery Worker** (for background neural analyses):
   ```bash
   PYTHONPATH=src uv run celery -A denoiser.workers.analysis_worker.celery_app worker --loglevel=info --pool=solo
   ```

### Frontend Environment Setup
The visualization dashboard is built using Next.js, React, TailwindCSS, and CSS design variables.

1. **Navigate to the frontend workspace**:
   ```bash
   cd web
   ```

2. **Install Node dependencies**:
   ```bash
   npm install
   ```

3. **Start Next.js local development server**:
   ```bash
   npm run dev
   ```
   The dashboard will load at `http://localhost:3000/app`.

---

## 🎨 Code Style and Quality Standards

### Python Quality (Ruff)
We enforce strict style checks and formatting using `ruff`.

- **Check Lints**:
  ```bash
  uv run ruff check .
  ```
- **Apply Auto-Fixes & Formatting**:
  ```bash
  uv run ruff format .
  ```

### TypeScript / Next.js Quality
- Maintain strictly typed files with `.ts` or `.tsx` extensions.
- Use predefined CSS variables inside Tailwind classes (`bg-[var(--bg-card)]`) instead of hardcoding raw hex values.
- Verify production bundles compile without warnings before opening a PR:
  ```bash
  npm run build
  ```

---

## 🧪 Testing Requirements

We enforce high test coverage. Ensure all unit and integration tests pass before submitting changes.

### Running Backend Tests (pytest)
```bash
# Run the complete Pytest suite
PYTHONPATH=src uv run python -m pytest
```

---

## 🌿 Git Branch & PR Workflow

### Branch Naming Conventions
- `feature/` for new capability additions (e.g., `feature/otlp-collector`)
- `bugfix/` for resolving existing platform defects (e.g., `bugfix/theme-flicker`)
- `docs/` for writing manuals and references (e.g., `docs/contributing-guide`)
- `review/` for dedicated release or wave evaluations (e.g., `review/phase-10`)

### Commit Messages
We encourage structural, clean commit titles adhering to conventional styles:
- `feat: add distributed trace flame graphs`
- `fix: eliminate browser alerts from settings panel`
- `docs: update deployment environment variable table`

### Pull Request Checklist
Before creating a PR, check that you have:
1. Validated that code builds completely without compilation or TypeScript errors.
2. Verified all `pytest` unit tests are green.
3. Formatted backend files using `ruff format .`.
4. Documented any public API modifications inside `README.md`.
