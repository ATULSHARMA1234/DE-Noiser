# Project To-Do List: Semantic Log De-Noiser

This document outlines the sequential steps to build the Semantic Log De-Noiser CLI, from environment setup to anomaly detection and reporting.

## Phase 1: Foundation & Project Scaffolding
- [x] **Task 1: Project Initialization**
  - Initialize a Python 3.12+ project using `uv` or `poetry`.
  - Set up the directory structure: `src/denoiser/`, `tests/`, `baselines/`, `data/`.
  - Configure `pyproject.toml` with core dependencies: `polars`, `sentence-transformers`, `hdbscan`, `lancedb`, `typer`, `rich`.
- [x] **Task 2: Configuration Framework**
  - Implement a `config.py` to manage default thresholds, model names (`all-MiniLM-L6-v2`), and local storage paths.
  - Set up logging and a custom exception hierarchy for the CLI.

## Phase 2: Data Ingestion & Preprocessing
- [x] **Task 3: Universal Log Reader**
  - Build a streaming `LogReader` that handles individual files, recursive directories, and basic `.log`/`.jsonl` parsing.
- [x] **Task 4: Stdin Integration**
  - Implement a non-blocking `StdinReader` to support `kubectl logs | semantic-log` patterns.
- [x] **Task 5: Privacy-First Redactor**
  - Create a regex-based `Redactor` to strip API keys, bearer tokens, emails, and PII before embedding.
- [x] **Task 6: Polars Normalization Engine**
  - Build a `Normalizer` using Polars to replace dynamic tokens (UUIDs, timestamps, IPs, memory addresses) with generic placeholders (e.g., `<UUID>`).
- [x] **Task 7: Semantic Deduplication**
  - Implement logic to group identical normalized messages to minimize calls to the embedding model.

## Phase 3: AI & Clustering Layer
- [x] **Task 8: Local Embedding Integration**
  - Integrate `SentenceTransformers` to convert normalized log messages into vector embeddings locally.
- [x] **Task 9: Persistent Embedding Cache**
  - Build a local SQLite or file-based cache to store embeddings of previously seen normalized templates.
- [x] **Task 10: HDBSCAN Clustering**
  - Implement the `LogClusterer` to group logs semantically without needing a predefined cluster count.
- [x] **Task 11: Cluster Metadata Extraction**
  - For each cluster, compute the centroid, identify a "representative example," and calculate size/severity distribution.

## Phase 4: Vector Storage & Baselines
- [x] **Task 12: LanceDB Vector Store Setup**
  - Initialize LanceDB as the local embedded database for persisting known log patterns.
- [x] **Task 13: Baseline Builder (`build-baseline`)**
  - Implement logic to export current clusters, centroids, and metadata into a versioned "baseline index" file.
- [x] **Task 14: Baseline Loader & Inspector**
  - Build utilities to load an existing baseline and print a summary of its contents for verification.

## Phase 5: Anomaly Detection Engine
- [x] **Task 15: Novelty Scoring Logic**
  - Implement Euclidean/Cosine distance calculations between new logs and the nearest baseline centroids.
- [x] **Task 16: Anomaly Classifier**
  - Define logic to label events as `KNOWN`, `RARE_KNOWN`, `NEW_PATTERN`, or `HIGH_RISK_ANOMALY` based on configurable distance thresholds.
- [x] **Task 17: Explainability Engine**
  - Implement `explain --cluster <ID>` to show the raw logs and nearest historical neighbor for a specific anomaly.

## Phase 6: CLI & Reporting
- [x] **Task 18: Typer CLI Scaffolding**
  - Define the command interface: `analyze`, `build-baseline`, `explain`.
- [x] **Task 19: Rich TUI Implementation**
  - Build beautiful terminal tables, progress bars, and colored severity outputs for the `analyze` command.
- [x] **Task 20: Reporting Suite**
  - Implement `JSONFormatter` for CI pipelines and `MarkdownFormatter` for incident postmortem documentation.
- [x] **Task 21: CI Exit Behavior**
  - Add the `--fail-on-anomaly` flag logic to return non-zero exit codes based on detection results.

---
**Next Step:** Proceed to **Task 1: Project Initialization**.
