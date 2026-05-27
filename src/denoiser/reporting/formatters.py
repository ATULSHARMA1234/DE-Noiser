"""
Reporting formatters for CLI output (JSON, Markdown, Rich Table).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from rich.console import Console
from rich.table import Table

from denoiser.clustering.models import Cluster
from denoiser.config import AnomalyLabel, OutputFormat
from denoiser.detection.models import AnomalyResult


class Reporter:
    """Formats and prints analysis results."""

    def __init__(self, console: Console) -> None:
        self.console = console

    def _color_for_label(self, label: AnomalyLabel) -> str:
        if label == AnomalyLabel.HIGH_RISK_ANOMALY:
            return "red"
        if label == AnomalyLabel.NEW_PATTERN:
            return "yellow"
        if label == AnomalyLabel.RARE_KNOWN:
            return "cyan"
        return "dim white"

    def print_table(
        self,
        clusters: list[Cluster],
        anomalies: dict[str, AnomalyResult] | None,
        top_n: int,
        total_logs: int,
        intelligence: dict[str, Any] | None = None,
    ) -> None:
        """Print results as a Rich table."""
        self.console.print(f"\n[bold]Semantic Log Analysis[/bold] ({total_logs} logs processed)\n")

        if intelligence:
            from rich.panel import Panel
            summary = intelligence.get("incident_summary", "N/A")
            domain = intelligence.get("failure_domain", "N/A")
            hints = intelligence.get("root_cause_hints", "N/A")

            content = f"[bold cyan]Failure Domain:[/bold cyan] {domain}\n\n[bold]Summary:[/bold]\n{summary}\n\n[bold]Next Steps / Hints:[/bold]\n{hints}"
            self.console.print(Panel(content, title="[bold sparkler]AI Incident Intelligence[/]", border_style="cyan"))
            self.console.print()

        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("Cluster", style="dim", width=8)
        table.add_column("Count", justify="right")
        table.add_column("Anomaly", width=15)
        table.add_column("Dist", justify="right")
        table.add_column("Semantic Summary", width=30)
        table.add_column("Source", style="cyan", width=25)
        table.add_column("Representative Log")

        # Sort by size descending, but maybe put anomalies first?
        # For now, just show the largest clusters, up to top_n.
        for i, c in enumerate(clusters[:top_n]):
            # Determine highest anomaly score in this cluster
            cluster_label_str = "-"
            cluster_dist_str = "-"
            color = "dim white"

            if anomalies:
                # Find worst anomaly in this cluster
                worst_result = None
                for t in c.templates:
                    res = anomalies.get(t)
                    if res:
                        if worst_result is None or res.distance > worst_result.distance:
                            worst_result = res

                if worst_result:
                    cluster_label_str = worst_result.label.value
                    cluster_dist_str = f"{worst_result.distance:.2f}"
                    color = self._color_for_label(worst_result.label)

            # Standalone Anomaly detection (Cluster -1 is noise/outlier)
            if c.cluster_id == -1 and cluster_label_str == "-":
                cluster_label_str = "OUTLIER"
                color = "red"

            cid_str = str(c.cluster_id) if c.cluster_id != -1 else "NOISE"

            # Truncate raw text for display - increase limit for "complete" view
            display_text = c.representative_raw.replace("\n", " ")
            if len(display_text) > 200:
                display_text = display_text[:197] + "..."

            # Get intelligence summary for this cluster if available
            semantic_summary = "-"
            if intelligence and "cluster_summaries" in intelligence:
                # clusters[:top_n] matches the order used in generate_summary
                summaries = intelligence["cluster_summaries"]
                if i < len(summaries):
                    semantic_summary = summaries[i]

            # Get source info from cluster
            source_info = f"{c.representative_source}:{c.representative_line}"

            table.add_row(
                cid_str,
                str(c.size),
                f"[{color}]{cluster_label_str}[/{color}]",
                cluster_dist_str,
                semantic_summary,
                source_info,
                display_text,
            )

        self.console.print(table)
        self.console.print()

    def print_json(
        self,
        clusters: list[Cluster],
        anomalies: dict[str, AnomalyResult] | None,
        total_logs: int,
        intelligence: dict[str, Any] | None = None,
    ) -> None:
        """Print results as JSON."""
        data = {
            "meta": {
                "timestamp": datetime.now(UTC).isoformat(),
                "total_logs": total_logs,
                "cluster_count": len(clusters),
            },
            "intelligence": intelligence,
            "clusters": [],
        }

        for c in clusters:
            cluster_data: dict[str, Any] = {
                "id": c.cluster_id,
                "size": c.size,
                "representative_template": c.representative_template,
                "representative_raw": c.representative_raw,
                "templates": c.templates,
            }
            if anomalies:
                # Attach anomaly info for the representative template
                res = anomalies.get(c.representative_template)
                if res:
                    cluster_data["anomaly"] = {
                        "label": res.label.value,
                        "distance": res.distance,
                        "nearest_cluster_id": res.nearest_cluster_id,
                    }
            data["clusters"].append(cluster_data)

        self.console.print_json(json.dumps(data))

    def print_markdown(
        self,
        clusters: list[Cluster],
        anomalies: dict[str, AnomalyResult] | None,
        top_n: int,
        total_logs: int,
        intelligence: dict[str, Any] | None = None,
    ) -> None:
        """Print results as a Markdown document."""
        lines = [
            "# Semantic Log Analysis Report",
            f"- **Timestamp:** {datetime.now(UTC).isoformat()}",
            f"- **Total Logs:** {total_logs}",
            f"- **Total Clusters:** {len(clusters)}",
            "",
        ]

        if intelligence:
            lines.extend([
                "## AI Incident Intelligence",
                f"**Failure Domain:** {intelligence.get('failure_domain', 'N/A')}",
                "",
                f"**Summary:** {intelligence.get('incident_summary', 'N/A')}",
                "",
                "**Root Cause Hints:**",
                str(intelligence.get('root_cause_hints', 'N/A')),
                "",
            ])

        lines.extend([
            "## Top Clusters",
            "",
            "| Cluster | Count | Anomaly | Dist | Summary | Source | Representative Log |",
            "|---------|-------|---------|------|---------|--------|--------------------|",
        ])

        for i, c in enumerate(clusters[:top_n]):
            label = "-"
            dist = "-"
            if anomalies:
                res = anomalies.get(c.representative_template)
                if res:
                    label = res.label.value
                    dist = f"{res.distance:.2f}"

            clean_raw = c.representative_raw.replace("\n", " ").replace("|", "\\|")
            if len(clean_raw) > 100:
                clean_raw = clean_raw[:97] + "..."

            cid = "NOISE" if c.cluster_id == -1 else str(c.cluster_id)

            # Get semantic summary
            semantic_summary = "-"
            if intelligence and "cluster_summaries" in intelligence:
                summaries = intelligence["cluster_summaries"]
                if i < len(summaries):
                    semantic_summary = summaries[i]

            # Get source info
            source_info = f"{c.representative_source}:{c.representative_line}"

            lines.append(f"| {cid} | {c.size} | {label} | {dist} | {semantic_summary} | {source_info} | `{clean_raw}` |")

        # Use rich's markdown rendering, or just print raw text.
        # Since this is intended for pipe to file, raw text is better.
        # But we must bypass rich's formatting so it's clean markdown.
        # Actually, using print(..., end="") to stdout bypasses Rich markup logic safely.
        import sys
        sys.stdout.write("\n".join(lines) + "\n\n")

    def report(
        self,
        format: OutputFormat,
        clusters: list[Cluster],
        anomalies: dict[str, AnomalyResult] | None,
        top_n: int,
        total_logs: int,
        intelligence: dict[str, Any] | None = None,
    ) -> None:
        """Dispatch to the correct formatting method."""
        if format == OutputFormat.JSON:
            self.print_json(clusters, anomalies, total_logs, intelligence)
        elif format == OutputFormat.MARKDOWN:
            self.print_markdown(clusters, anomalies, top_n, total_logs, intelligence)
        else:
            self.print_table(clusters, anomalies, top_n, total_logs, intelligence)
