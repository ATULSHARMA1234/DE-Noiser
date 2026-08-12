"""
Issue tracking — the durable identity behind a log pattern.

An analysis run produces clusters, and a cluster dies with its run: HDBSCAN
renumbers ``cluster_id`` every time, so the same failing pattern was reported as
new on each run and the product could never answer "is this new?", "is it
getting worse?", "who is looking at it?". This module folds each run's clusters
into ``log_issues`` rows keyed on a fingerprint of the pattern itself, carrying
first/last seen, a merged occurrence histogram, tag prevalence and samples.

Matching is fingerprint-first with a template-hash fallback: a cluster's
representative template is whichever one sits closest to the centroid, and that
can change between runs even when the cluster has not, so an exact miss falls
back to intersecting the templates the issue has already absorbed.
"""

from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta

from denoiser.logging import get_logger
from denoiser.storage.db import IssueEvent, LogIssue
from denoiser.utils.time import utcnow

logger = get_logger(__name__)

# Raw samples kept per issue for the detail panel's Previous/Next.
MAX_SAMPLES = 20
# Template hashes kept per issue. Bounded so a churning cluster cannot grow the
# row without limit; the newest templates are the ones worth matching on.
MAX_TEMPLATE_HASHES = 200
# Histogram retention. Hourly buckets, four weeks — enough to show a trend
# without the JSON column growing unbounded.
HISTOGRAM_RETENTION = timedelta(days=28)

# Metadata keys that describe *where* a log came from rather than what it says.
# These make useful facets; free-form keys (request ids, durations) do not.
TAG_KEYS = ("service", "level", "env", "environment", "host", "container", "pod",
            "namespace", "version", "region", "source_label")
# A tag value seen on nearly every line of a cluster says something; one seen
# once is noise in the panel.
MIN_TAG_PCT = 5.0


def hash_template(template: str) -> str:
    """Stable short hash of a single normalized template."""
    return hashlib.sha256((template or "").encode("utf-8", "replace")).hexdigest()[:12]


def fingerprint(service: str | None, template: str | None) -> str:
    """The issue key: a pattern in one service is not the same issue as the
    identical pattern in another, because they are owned and fixed separately."""
    payload = f"{(service or 'unknown').strip().lower()}|{(template or '').strip()}"
    return hashlib.sha256(payload.encode("utf-8", "replace")).hexdigest()[:16]


def _bucket(dt: datetime) -> str:
    """Hourly bucket key. Naive timestamps are treated as UTC — every writer in
    the pipeline stores UTC, and mixing naive and aware keys splits a bucket."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).replace(minute=0, second=0, microsecond=0).isoformat()


#: Window a log's own claimed timestamp has to fall inside to be used for
#: trend maths. Mirrors TimestampExtractor's bounds; applied again here because
#: timestamps also arrive on structured records that never passed through the
#: extractor.
_MAX_TIMESTAMP_AGE = timedelta(days=365 * 10)
_MAX_TIMESTAMP_SKEW = timedelta(days=1)


def _as_utc(dt: datetime | None) -> datetime | None:
    """Aware UTC, reading a naive value as UTC (what every writer here stores).

    Timestamps outside a plausible window are treated as absent. ``last_seen``
    is a running max across every event folded into an issue, so one line from a
    host with a broken clock — year 9999 is the common shape — would otherwise
    pin the issue's last-seen date millennia ahead and permanently satisfy every
    recency filter, sparkline window and SLO evaluation that reads it.
    """
    if dt is None:
        return None
    aware = dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)

    now = datetime.now(UTC)
    if not (now - _MAX_TIMESTAMP_AGE) <= aware <= (now + _MAX_TIMESTAMP_SKEW):
        return None
    return aware


def _naive_utc(dt: datetime | None) -> datetime | None:
    """Naive UTC — the form the ``DateTime`` columns hold. Mixing the two forms
    raises on comparison, so timestamps are normalized at the boundary."""
    aware = _as_utc(dt)
    return aware.replace(tzinfo=None) if aware else None


def summarize_records(records: list) -> dict:
    """Facts a detail panel needs about one cluster's member log lines.

    Returns tag prevalence, an hourly histogram, first/last seen and a bounded
    set of samples. Records are ``LogRecord``s; anything missing a timestamp is
    still counted, it just cannot contribute to the trend.
    """
    tag_counts: dict[str, Counter] = defaultdict(Counter)
    buckets: Counter = Counter()
    samples: list[dict] = []
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    total = 0

    for record in records:
        total += 1
        metadata = getattr(record, "metadata", None) or {}
        for key in TAG_KEYS:
            value = metadata.get(key)
            if value not in (None, "", []):
                tag_counts[key][str(value)] += 1

        ts = _as_utc(getattr(record, "timestamp", None))
        if ts is not None:
            buckets[_bucket(ts)] += 1
            first_seen = ts if first_seen is None or ts < first_seen else first_seen
            last_seen = ts if last_seen is None or ts > last_seen else last_seen

        if len(samples) < MAX_SAMPLES:
            samples.append({
                "raw": getattr(record, "raw_text", ""),
                "timestamp": ts.isoformat() if ts else None,
                "source": getattr(record, "source", None),
                "line_number": getattr(record, "line_number", None),
                "metadata": {k: v for k, v in metadata.items() if k in TAG_KEYS},
            })

    tags: dict[str, list[dict]] = {}
    for key, counter in tag_counts.items():
        values = []
        for value, count in counter.most_common(5):
            pct = (count / total * 100) if total else 0.0
            if pct >= MIN_TAG_PCT:
                values.append({"value": value, "count": count, "pct": round(pct, 1)})
        if values:
            tags[key] = values

    return {
        "tags": tags,
        "histogram": [{"ts": ts, "count": c} for ts, c in sorted(buckets.items())],
        "samples": samples,
        # Naive UTC, matching the DateTime columns these end up in.
        "first_seen": _naive_utc(first_seen),
        "last_seen": _naive_utc(last_seen),
        "event_count": total,
    }


def _merge_histogram(existing: list | None, incoming: list | None) -> list:
    """Sum two hourly histograms and drop buckets older than the retention window."""
    merged: Counter = Counter()
    for series in (existing or [], incoming or []):
        for point in series:
            ts = point.get("ts")
            if ts:
                merged[ts] += int(point.get("count") or 0)

    cutoff = (utcnow().replace(tzinfo=UTC) - HISTOGRAM_RETENTION).isoformat()
    return [
        {"ts": ts, "count": count}
        for ts, count in sorted(merged.items())
        if ts >= cutoff
    ]


def _service_of(cluster_data: dict) -> str:
    """The owning service, as the analysis pipeline records it: `source:line`."""
    source = str(cluster_data.get("source") or "")
    return source.split(":")[0] or "unknown"


def _title_of(cluster_data: dict) -> str:
    """Prefer the LLM summary; fall back to the template, then the raw line.

    The summary can arrive as the placeholder "Analyzing..." or as a dict from
    the intelligence layer, and neither is a title.
    """
    summary = cluster_data.get("summary")
    if isinstance(summary, dict):
        summary = summary.get("summary") or summary.get("representative_log")
    if isinstance(summary, str) and summary.strip() and summary != "Analyzing...":
        return summary.strip()[:500]
    template = cluster_data.get("representative_template")
    if template:
        return str(template)[:500]
    return str(cluster_data.get("representative_log") or "Unlabelled pattern")[:500]


def _find_issue(db, tenant_id, service: str, fp: str, template_hashes: list[str]):
    """Existing issue for this pattern, by fingerprint then by template overlap."""
    query = db.query(LogIssue).filter(LogIssue.tenant_id == tenant_id)
    match = query.filter(LogIssue.fingerprint == fp).first()
    if match:
        return match

    if not template_hashes:
        return None

    incoming = set(template_hashes)
    for candidate in query.filter(LogIssue.service == service).all():
        if incoming & set(candidate.template_hashes or []):
            return candidate
    return None


def upsert_issues(db, tenant_id, run_id: str, formatted_clusters: list[dict],
                  clusters: list | None = None, groups: dict | None = None) -> dict:
    """Fold one run's clusters into the tenant's issues.

    ``clusters`` are the clusterer's objects (for their template lists) and
    ``groups`` maps template → member ``LogRecord``s; both are optional so a
    caller that only has the serialized snapshot still gets issue identity,
    just without tags, histogram or samples.

    Returns counts of what was created and updated. Never raises: issue
    tracking is an enrichment, and a failure here must not lose the run.
    """
    groups = groups or {}
    templates_by_cluster: dict[int, list[str]] = {}
    for cluster in clusters or []:
        templates_by_cluster[cluster.cluster_id] = list(getattr(cluster, "templates", None) or [])

    created = updated = 0
    now = utcnow()

    for cluster_data in formatted_clusters:
        try:
            cluster_id = cluster_data.get("cluster_id")
            template = cluster_data.get("representative_template") or ""
            service = _service_of(cluster_data)
            templates = templates_by_cluster.get(cluster_id) or ([template] if template else [])

            records = []
            for member_template in templates:
                records.extend(groups.get(member_template, []))

            facts = summarize_records(records)
            event_count = facts["event_count"] or int(cluster_data.get("size") or 0)
            hashes = [hash_template(t) for t in templates][:MAX_TEMPLATE_HASHES]
            fp = fingerprint(service, template)

            issue = _find_issue(db, tenant_id, service, fp, hashes)
            first_seen = facts["first_seen"] or now
            last_seen = facts["last_seen"] or now

            if issue is None:
                issue = LogIssue(
                    tenant_id=tenant_id,
                    fingerprint=fp,
                    template_hashes=hashes,
                    title=_title_of(cluster_data),
                    template=template,
                    representative_log=cluster_data.get("representative_log"),
                    service=service,
                    severity=cluster_data.get("priority") or "P3",
                    state="FOR_REVIEW",
                    first_seen=first_seen,
                    last_seen=last_seen,
                    total_events=event_count,
                    run_count=1,
                    last_run_id=run_id,
                    last_cluster_id=cluster_id,
                    anomaly_score=float(cluster_data.get("anomaly_score") or 0.0),
                    is_noise=cluster_id == -1,
                    tags=facts["tags"],
                    histogram=facts["histogram"],
                    samples=facts["samples"],
                )
                db.add(issue)
                db.flush()
                db.add(IssueEvent(
                    tenant_id=tenant_id, issue_id=issue.id, kind="seen",
                    detail={"run_id": run_id, "events": event_count, "first": True},
                ))
                created += 1
                continue

            # Merge, keeping the union of what each run knew. A resolved issue
            # that recurs is a regression: it goes back on the review queue
            # rather than silently staying closed.
            previous_state = issue.state
            issue.template_hashes = list(dict.fromkeys((issue.template_hashes or []) + hashes))[:MAX_TEMPLATE_HASHES]
            issue.title = _title_of(cluster_data) if issue.title.startswith("Analyzing") else issue.title
            issue.template = template or issue.template
            issue.representative_log = cluster_data.get("representative_log") or issue.representative_log
            issue.severity = cluster_data.get("priority") or issue.severity
            issue.first_seen = min(_naive_utc(issue.first_seen) or first_seen, first_seen)
            issue.last_seen = max(_naive_utc(issue.last_seen) or last_seen, last_seen)
            issue.total_events = (issue.total_events or 0) + event_count
            issue.run_count = (issue.run_count or 0) + 1
            issue.last_run_id = run_id
            issue.last_cluster_id = cluster_id
            issue.anomaly_score = float(cluster_data.get("anomaly_score") or issue.anomaly_score or 0.0)
            issue.tags = facts["tags"] or issue.tags
            issue.histogram = _merge_histogram(issue.histogram, facts["histogram"])
            if facts["samples"]:
                issue.samples = facts["samples"]
            issue.updated_at = now

            regressed = previous_state == "RESOLVED"
            if regressed:
                issue.state = "FOR_REVIEW"

            db.add(IssueEvent(
                tenant_id=tenant_id, issue_id=issue.id,
                kind="regression" if regressed else "seen",
                detail={"run_id": run_id, "events": event_count},
            ))
            updated += 1
        except Exception as e:  # one bad cluster must not sink the rest
            logger.warning(f"Issue upsert failed for cluster {cluster_data.get('cluster_id')}: {e}")

    logger.info("Issue tracking: %d created, %d updated (run %s)", created, updated, run_id)
    return {"created": created, "updated": updated}


def suspect_deployment(db, tenant_id, issue: LogIssue, window_hours: int = 24) -> dict | None:
    """The deploy most likely to have introduced this issue.

    The last deployment marker for the issue's service at or before it was first
    seen, within a window — the log analogue of Datadog's suspect commit. The
    markers are already written by the GitHub integration; nothing here consults
    an external service.
    """
    from denoiser.storage.db import DeploymentMarker

    naive_first_seen = _naive_utc(issue.first_seen)
    if naive_first_seen is None:
        return None

    marker = (
        db.query(DeploymentMarker)
        .filter(
            DeploymentMarker.tenant_id == tenant_id,
            DeploymentMarker.timestamp <= naive_first_seen,
            DeploymentMarker.timestamp >= naive_first_seen - timedelta(hours=window_hours),
        )
        .order_by(DeploymentMarker.timestamp.desc())
        .first()
    )
    if marker is None:
        return None

    delta = naive_first_seen - marker.timestamp
    return {
        "id": marker.id,
        "service": marker.service,
        "version": marker.version,
        "environment": marker.environment,
        "description": marker.description,
        "timestamp": marker.timestamp.isoformat(),
        "minutes_before_first_seen": int(delta.total_seconds() // 60),
    }
