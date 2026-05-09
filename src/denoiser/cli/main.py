"""
Semantic Log De-Noiser — CLI entry point.

Commands:
    analyze          Ingest, normalize, cluster, and detect anomalies in logs.
    build-baseline   Create a historical baseline index from known-good logs.
    update-baseline  Merge new known-good logs into an existing baseline.
    inspect-baseline Print a summary of a baseline index.
    explain          Show detailed context for a specific cluster or anomaly.
"""

from __future__ import annotations

import sys
import typer
from rich.console import Console

from denoiser.baselines.manager import BaselineManager
from denoiser.clustering.hdbscan_clusterer import LogClusterer
from denoiser.config import AnomalyLabel, AnalysisMode, OutputFormat, settings
from denoiser.detection.scorer import AnomalyScorer
from denoiser.embeddings.provider import LocalEmbeddingProvider
from denoiser.exceptions import AnomalyThresholdExceeded, EXIT_CODE_SUCCESS, EXIT_CODE_ANOMALY_THRESHOLD
from denoiser.ingestion.reader import LogReader
from denoiser.ingestion.stdin import StdinReader
from denoiser.integrations.aws import CloudWatchReader
from denoiser.integrations.k8s import KubernetesReader
from denoiser.integrations.slack import SlackNotifier
from denoiser.integrations.storage import S3Storage
from denoiser.intelligence.llm import IncidentIntelligence
from denoiser.logging import get_logger, setup_logging
from denoiser.preprocessing.deduplication import Deduplicator
from denoiser.preprocessing.normalization import Normalizer
from denoiser.preprocessing.redaction import Redactor
from denoiser.reporting.formatters import Reporter
from denoiser.storage.database import init_db, save_analysis
import os
import time
from datetime import datetime

# Initialize the persistent database
init_db()

app = typer.Typer(
    name="semantic-log",
    help="Semantic Log De-Noiser — cluster noisy logs, detect anomalies, triage faster.",
    add_completion=False,
    rich_markup_mode="rich",
)

console = Console(stderr=True)
logger = get_logger("cli")


# ── analyze ──────────────────────────────────────────────────────────────────
@app.command()
def analyze(
    source: str = typer.Argument(
        ...,
        help="Path to a log file, directory, or '-' for stdin.",
    ),
    baseline: str | None = typer.Option(
        None, "--baseline", "-b",
        help="Path to a baseline index for anomaly comparison.",
    ),
    top: int = typer.Option(
        10, "--top", "-n",
        help="Number of top clusters / anomalies to display.",
    ),
    format: str = typer.Option(
        "table", "--format", "-f",
        help="Output format: table, json, or markdown.",
    ),
    mode: str = typer.Option(
        "general", "--mode", "-m",
        help="Analysis mode: general, security, or performance.",
    ),
    fail_on_anomaly: str | None = typer.Option(
        None, "--fail-on-anomaly",
        help="Exit non-zero if anomalies meet threshold: low, medium, high.",
    ),
    local_only: bool = typer.Option(
        False, "--local-only",
        help="Use only local models; never send data externally.",
    ),
    redact: bool = typer.Option(
        True, "--redact/--no-redact",
        help="Redact secrets and PII before processing.",
    ),
    intelligence: bool = typer.Option(
        True, "--intelligence",
        help="Enable LLM-based incident intelligence and root-cause hints.",
    ),
    slack_webhook: str | None = typer.Option(
        None, "--slack-webhook",
        help="Slack Webhook URL to post the report.",
    ),
    org_id: str | None = typer.Option(
        None, "--org",
        help="The organization ID for tenant isolation.",
    ),
    team_id: str | None = typer.Option(
        None, "--team",
        help="The team ID for tenant isolation.",
    ),
) -> None:
    # 1. Update config based on CLI options
    # We do this dynamically for the current run
    try:
        preset_mode = AnalysisMode(mode)
    except ValueError:
        console.print(f"[bold red]Invalid mode '{mode}'[/bold red]")
        sys.exit(1)

    try:
        out_fmt = OutputFormat(format)
    except ValueError:
        console.print(f"[bold red]Invalid format '{format}'[/bold red]")
        sys.exit(1)

    # Override config if intelligence flag passed
    if intelligence:
        settings.llm_enabled = True

    # 2. Ingestion
    if source == "-":
        reader = StdinReader()
        records_iter = reader.read()
    elif source.startswith("k8s://"):
        # Format: k8s://namespace/pod_name
        try:
            parts = source.replace("k8s://", "").split("/")
            if len(parts) != 2:
                raise ValueError("Source must be in format k8s://namespace/pod_name")
            namespace, pod_name = parts
            reader = KubernetesReader()
            records_iter = reader.read(namespace, pod_name)
        except Exception as e:
            console.print(f"[bold red]Kubernetes Error:[/bold red] {e}")
            sys.exit(1)
    elif source.startswith("aws://"):
        # Format: aws://log_group[/log_stream]
        try:
            path = source.replace("aws://", "")
            if "/" in path:
                log_group, log_stream = path.split("/", 1)
            else:
                log_group, log_stream = path, None
            
            reader = CloudWatchReader()
            records_iter = reader.read(log_group, log_stream)
        except Exception as e:
            console.print(f"[bold red]AWS Error:[/bold red] {e}")
            sys.exit(1)
    else:
        reader = LogReader()
        records_iter = reader.read(source)

    # 3. Preprocessing
    redactor = Redactor(enabled=redact)
    normalizer = Normalizer()
    deduper = Deduplicator()
    
    BATCH_SIZE = 10000
    batch = []

    with console.status("[bold green]Ingesting and normalizing logs (Batch Mode)...[/bold green]"):
        for record in records_iter:
            batch.append(record)
            if len(batch) >= BATCH_SIZE:
                texts = [r.raw_text for r in batch]
                redacted = [redactor.redact(t) for t in texts]
                normalized = normalizer.normalize_batch(redacted)
                for i, r in enumerate(batch):
                    r.normalized_text = normalized[i]
                    deduper.add(r)
                batch = []
        
        if batch:
            texts = [r.raw_text for r in batch]
            redacted = [redactor.redact(t) for t in texts]
            normalized = normalizer.normalize_batch(redacted)
            for i, r in enumerate(batch):
                r.normalized_text = normalized[i]
                deduper.add(r)

    unique_templates = deduper.get_unique_templates()
    if not unique_templates:
        console.print("[yellow]No logs found to process.[/yellow]")
        return

    # 4. Embeddings
    with console.status("[bold blue]Generating embeddings...[/bold blue]"):
        embedder = LocalEmbeddingProvider()
        vectors = embedder.embed(unique_templates)

    # 5. Clustering
    with console.status("[bold cyan]Clustering logs...[/bold cyan]"):
        clusterer = LogClusterer()
        clusters = clusterer.fit_predict(
            unique_templates, vectors, deduper.get_all_groups(), deduper.get_all_counts()
        )

    # 6. Detection (Optional)
    anomalies = None
    max_severity = None
    if baseline:
        with console.status("[bold yellow]Scoring anomalies against baseline...[/bold yellow]"):
            bm = BaselineManager(baseline)
            scorer = AnomalyScorer(bm)
            results = scorer.score_batch(unique_templates, vectors)
            anomalies = {res.template: res for res in results}
            
            # Track highest severity for exit codes
            for res in results:
                if max_severity is None:
                    max_severity = res.label
                elif res.label == AnomalyLabel.HIGH_RISK_ANOMALY:
                    max_severity = res.label
                elif res.label == AnomalyLabel.NEW_PATTERN and max_severity != AnomalyLabel.HIGH_RISK_ANOMALY:
                    max_severity = res.label

    # 7. Intelligence (Optional)
    llm_payload = None
    if settings.llm_enabled:
        with console.status("[bold sparkler]Generating AI Incident Intelligence...[/bold sparkler]"):
            intel = IncidentIntelligence()
            llm_payload = intel.generate_summary(clusters, anomalies, top_n=top)

    # 8. Reporting
    reporter = Reporter(Console()) # use stdout for data
    reporter.report(out_fmt, clusters, anomalies, top, deduper.total_count, llm_payload)

    # 9. Slack Notification (Optional)
    if slack_webhook:
        notifier = SlackNotifier(slack_webhook)
        slack_report = notifier.format_report_for_slack(
            total_logs=deduper.total_count,
            intelligence=llm_payload,
            anomalies_found=(anomalies is not None)
        )
        notifier.notify(slack_report)

    # 10. Exit logic
    save_analysis(source, deduper.total_count, llm_payload or {}, clusters, anomalies)
    
    if fail_on_anomaly and max_severity:
        fail_level = AnomalyLabel(fail_on_anomaly.lower())
        # Ordered severity check hack
        levels = [AnomalyLabel.KNOWN, AnomalyLabel.RARE_KNOWN, AnomalyLabel.NEW_PATTERN, AnomalyLabel.HIGH_RISK_ANOMALY]
        if levels.index(max_severity) >= levels.index(fail_level):
            raise AnomalyThresholdExceeded(
                f"Anomaly threshold exceeded. Found: {max_severity.value}"
            )


# ── agent (THE NERVOUS SYSTEM) ────────────────────────────────────────────────
@app.command()
def agent(
    path: str = typer.Argument(..., help="Path to the log file to monitor."),
    interval: int = typer.Option(10, "--interval", "-i", help="Batch interval in seconds."),
):
    """
    [THE NERVOUS SYSTEM]
    Monitor a log file in real-time and push semantic insights to the dashboard.
    """
    console.print(f"\n[bold cyan]⚡ Semantic Agent Activated[/bold cyan]")
    console.print(f"[dim]Monitoring:[/dim] {path}")
    console.print(f"[dim]Interval:[/dim] {interval}s batches\n")

    last_size = 0
    if os.path.exists(path):
        last_size = os.path.getsize(path)

    try:
        while True:
            current_size = os.path.getsize(path) if os.path.exists(path) else 0
            if current_size > last_size:
                console.print(f"[{datetime.now().strftime('%H:%M:%S')}] 📥 [bold]Signal Detected[/bold] - Triggering Batch Analysis...")
                # Run a mini-analysis on the new data
                # For the demo, we just trigger the full analyze logic on the file
                analyze(source=path, intelligence=True, format="table")
                last_size = current_size
            
            time.sleep(interval)
    except KeyboardInterrupt:
        console.print("\n[bold yellow]Agent Deactivated.[/bold yellow]")


# ── build-baseline ───────────────────────────────────────────────────────────
@app.command("build-baseline")
def build_baseline(
    source: str = typer.Argument(
        ...,
        help="Path to known-good log files or directory.",
    ),
    output: str = typer.Option(
        "baseline.index", "--output", "-o",
        help="Output path for the baseline index.",
    ),
) -> None:
    reader = LogReader()
    redactor = Redactor(enabled=True)
    normalizer = Normalizer()
    deduper = Deduplicator()

    with console.status("[bold green]Processing known-good logs...[/bold green]"):
        for record in reader.read(source):
            redacted = redactor.redact(record.raw_text)
            record.normalized_text = normalizer.normalize_single(redacted)
            deduper.add(record)

    unique_templates = deduper.get_unique_templates()
    if not unique_templates:
        console.print("[red]No logs found to build baseline.[/red]")
        sys.exit(1)

    with console.status("[bold blue]Embedding...[/bold blue]"):
        embedder = LocalEmbeddingProvider()
        vectors = embedder.embed(unique_templates)

    with console.status("[bold cyan]Clustering...[/bold cyan]"):
        clusterer = LogClusterer()
        clusters = clusterer.fit_predict(
            unique_templates, vectors, deduper.get_all_groups()
        )

    with console.status("[bold magenta]Saving index...[/bold magenta]"):
        bm = BaselineManager(output)
        bm.build(clusters, deduper.total_count)

    console.print(f"[bold green]✔ Baseline {output} built successfully![/bold green]")


# ── update-baseline ──────────────────────────────────────────────────────────
@app.command("update-baseline")
def update_baseline(
    baseline: str = typer.Argument(..., help="Path to existing baseline index."),
    source: str = typer.Argument(..., help="Path to new known-good logs to merge."),
) -> None:
    """Merge new known-good logs into an existing baseline."""
    console.print(f"[bold cyan]▶ Updating baseline[/bold cyan] {baseline} with {source}")
    console.print("[dim]Not yet implemented.[/dim]")


# ── inspect-baseline ─────────────────────────────────────────────────────────
@app.command("inspect-baseline")
def inspect_baseline(
    baseline: str = typer.Argument(..., help="Path to baseline index."),
) -> None:
    bm = BaselineManager(baseline)
    try:
        meta = bm.get_metadata()
        table = bm.load()
        
        labeled_count = table.search().where("label IS NOT NULL").to_list()
        ack_count = table.search().where("is_acknowledged = true").to_list()

        console.print(f"[bold]Baseline:[/bold] {baseline}")
        console.print(f"  [dim]Created:[/dim] {meta.created_at}")
        console.print(f"  [dim]Model:[/dim] {meta.embedding_model}")
        console.print(f"  [dim]Clusters:[/dim] {meta.cluster_count}")
        console.print(f"  [dim]Labeled:[/dim] {len(labeled_count)}")
        console.print(f"  [dim]Acknowledged:[/dim] {len(ack_count)}")
        console.print(f"  [dim]Logs Processed:[/dim] {meta.total_logs_processed}")
    except Exception as e:
        console.print(f"[bold red]Error inspecting baseline:[/bold red] {e}")


# ── explain ──────────────────────────────────────────────────────────────────
@app.command()
def explain(
    cluster: str = typer.Option(
        ..., "--cluster", "-c",
        help="Cluster ID to explain (e.g. 0, 1, 2).",
    ),
    baseline: str = typer.Option(
        "baseline.index", "--baseline", "-b",
        help="Path to the baseline index.",
    ),
) -> None:
    """Show detailed context for a specific cluster from a baseline."""
    console.print(f"[bold yellow]▶ Explaining cluster[/bold yellow] {cluster} [dim]from {baseline}[/dim]")
    
    try:
        cluster_id = int(cluster)
    except ValueError:
        console.print(f"[bold red]Invalid cluster ID:[/bold red] '{cluster}' must be an integer.")
        sys.exit(1)

    bm = BaselineManager(baseline)
    try:
        table = bm.load()
        # Query the exact cluster
        results = table.search().where(f"cluster_id = {cluster_id}").to_list()
        
        if not results:
            console.print(f"[yellow]Cluster {cluster_id} not found in baseline.[/yellow]")
            return
            
        record = results[0]
        console.print(f"\n[bold cyan]Cluster {cluster_id} Metadata:[/bold cyan]")
        console.print(f"  [dim]Label:[/dim] [bold green]{record.get('label') or 'None'}[/bold green]")
        console.print(f"  [dim]Status:[/dim] {'✅ Acknowledged' if record.get('is_acknowledged') else '⚠️ Unacknowledged'}")
        console.print(f"  [dim]Size (Raw Logs):[/dim] {record.get('size', 'N/A')}")
        
        console.print(f"\n[bold]Representative Template (Normalized):[/bold]")
        console.print(f"  {record.get('representative_template', 'N/A')}")
        
        console.print(f"\n[bold]Representative Raw Log:[/bold]")
        console.print(f"  {record.get('representative_raw', 'N/A')}\n")
        
    except Exception as e:
        console.print(f"[bold red]Error explaining cluster:[/bold red] {e}")


# ── label ────────────────────────────────────────────────────────────────────
@app.command()
def label(
    cluster: int = typer.Argument(..., help="The cluster ID to label."),
    name: str = typer.Argument(..., help="The human-readable label for this pattern."),
    baseline: str = typer.Option("baseline.index", "--baseline", "-b", help="Path to baseline index."),
) -> None:
    """Assign a human-readable name to a semantic cluster."""
    bm = BaselineManager(baseline)
    if bm.update_cluster_metadata(cluster, label=name):
        console.print(f"[bold green]✔ Cluster {cluster} labeled as '{name}'[/bold green]")
    else:
        console.print(f"[bold red]Failed to label cluster {cluster}[/bold red]")
        sys.exit(1)


# ── acknowledge ──────────────────────────────────────────────────────────────
@app.command()
def acknowledge(
    cluster: int = typer.Argument(..., help="The cluster ID to acknowledge."),
    baseline: str = typer.Option("baseline.index", "--baseline", "-b", help="Path to baseline index."),
    undo: bool = typer.Option(False, "--undo", help="Mark the cluster as unacknowledged."),
) -> None:
    """Mark a cluster as 'known-safe' so it no longer triggers high-risk alerts."""
    bm = BaselineManager(baseline)
    status = not undo
    if bm.update_cluster_metadata(cluster, is_acknowledged=status):
        msg = "acknowledged" if status else "unacknowledged"
        console.print(f"[bold green]✔ Cluster {cluster} marked as {msg}[/bold green]")
    else:
        console.print(f"[bold red]Failed to update cluster {cluster}[/bold red]")
        sys.exit(1)


# ── push-baseline ────────────────────────────────────────────────────────────
@app.command("push-baseline")
def push_baseline(
    baseline: str = typer.Argument(..., help="Path to local baseline index."),
    name: str = typer.Argument(..., help="Remote name for the baseline (e.g. 'prod-v1')."),
    bucket: str | None = typer.Option(None, "--bucket", help="S3 bucket name."),
) -> None:
    """Upload a local baseline to shared S3 storage."""
    storage = S3Storage(bucket)
    if storage.push(Path(baseline), name):
        console.print(f"[bold green]✔ Baseline {baseline} pushed to S3 as '{name}'[/bold green]")
    else:
        sys.exit(1)


# ── pull-baseline ────────────────────────────────────────────────────────────
@app.command("pull-baseline")
def pull_baseline(
    name: str = typer.Argument(..., help="Remote name of the baseline to pull."),
    output: str = typer.Option("baseline.index", "--output", "-o", help="Local output path."),
    bucket: str | None = typer.Option(None, "--bucket", help="S3 bucket name."),
) -> None:
    """Download a shared baseline from S3 storage."""
    storage = S3Storage(bucket)
    if storage.pull(name, Path(output)):
        console.print(f"[bold green]✔ Shared baseline '{name}' pulled to {output}[/bold green]")
    else:
        sys.exit(1)


if __name__ == "__main__":
    try:
        setup_logging()
        app()
    except Exception as e:
        logger.exception("Fatal error")
        exit_code = getattr(e, "exit_code", EXIT_CODE_SUCCESS if not isinstance(e, Exception) else 1)
        sys.exit(exit_code)
