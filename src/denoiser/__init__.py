"""
Semantic Log De-Noiser
======================

CLI tool for semantic log clustering, noise reduction, and out-of-distribution
anomaly detection. Designed for SREs, platform engineers, backend developers,
DevOps teams, and security analysts.

Modules
-------
- ingestion    : Log reading from files, directories, and stdin
- preprocessing: Normalization, redaction, and deduplication
- embeddings   : Vector embedding generation and caching
- clustering   : HDBSCAN/DBSCAN semantic clustering
- baselines    : Historical baseline creation, storage, and loading
- detection    : Out-of-distribution anomaly detection and scoring
- cli          : Typer-based command interface
- reporting    : JSON, Markdown, and table output formatters
"""

__version__ = "0.1.0"
__app_name__ = "semantic-log-denoiser"
