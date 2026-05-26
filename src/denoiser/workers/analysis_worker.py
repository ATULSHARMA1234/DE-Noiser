import os
import time
import uuid
import json
from pathlib import Path
from datetime import datetime, timezone
import polars as pl
from celery import Celery

from denoiser.logging import get_logger
from denoiser.config import settings
from denoiser.storage.db import SessionLocal, Incident, AnalysisRun
from denoiser.storage.vector_store import VectorStore

from denoiser.cli.main import (
    Redactor, Normalizer, Deduplicator, LogReader, 
    LocalEmbeddingProvider, LogClusterer, BaselineManager, 
    AnomalyScorer, IncidentIntelligence
)
from denoiser.preprocessing.timestamp import TimestampExtractor
from denoiser.detection.causal_scorer import CausalScorer
from denoiser.detection.severity import SeverityScorer
from denoiser.detection.metrics_correlator import MetricsCorrelator

logger = get_logger(__name__)

# Initialize Celery using local Redis if URL not provided
redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
celery_app = Celery('analysis_worker', broker=redis_url, backend=redis_url)
celery_app.conf.update(task_track_started=True, result_expires=86400)

# Setup LanceDB
vector_store = VectorStore()

@celery_app.task(bind=True)
def run_analysis_task(self, request_dict: dict):
    """
    Background Celery task that performs heavy log ingestion, deduplication,
    embedding, clustering, anomaly detection, LLM analysis, and DB persistence.
    """
    logger.info(f"Starting async analysis task: {self.request.id}")
    self.update_state(state='PROGRESS', meta={'progress': 10, 'status': 'Ingesting files'})
    
    start_time = time.time()
    
    # Unpack request
    sources = request_dict.get("sources", [])
    if not sources and "source" in request_dict:
        sources = [request_dict.get("source")]
    
    baseline = request_dict.get("baseline")
    intelligence = request_dict.get("intelligence", False)
    top_n = request_dict.get("top_n", 3)
    
    # 1. Ingestion
    timestamp_extractor = TimestampExtractor()
    records_data = []
    reader = LogReader()

    for src in sources:
        source_label = Path(src).stem
        try:
            for record in reader.read(src):
                epoch_ms = timestamp_extractor.extract(record.raw_text)
                dt = (
                    datetime.fromtimestamp(epoch_ms / 1000.0, timezone.utc)
                    if epoch_ms is not None
                    else None
                )
                
                records_data.append({
                    "raw_text": record.raw_text,
                    "source_path": record.source,
                    "source_label": source_label,
                    "line_number": record.line_number,
                    "timestamp": dt,
                    "timestamp_ms": epoch_ms or 0,
                    "metadata": json.dumps(record.metadata)
                })
        except Exception as e:
            logger.warning(f"Failed to read source {src}: {e}")

    if not records_data:
        return {"status": "error", "message": "No logs found at source(s)"}

    self.update_state(state='PROGRESS', meta={'progress': 30, 'status': 'Redacting and Normalizing'})
    df = pl.DataFrame(records_data)
    
    # 3. Apply Redaction and Normalization
    redactor = Redactor(enabled=True)
    normalizer = Normalizer()
    
    raw_texts = df["raw_text"].to_list()
    redacted_texts = [redactor.redact(t) for t in raw_texts]
    normalized_texts = normalizer.normalize_batch(redacted_texts)
    df = df.with_columns(pl.Series("normalized_text", normalized_texts))

    # 4. Deduplication
    deduper = Deduplicator()
    from denoiser.ingestion.models import LogRecord
    for row in df.iter_rows(named=True):
        meta = json.loads(row["metadata"]) if row["metadata"] else {}
        meta["source_label"] = row["source_label"]

        rec = LogRecord(
            raw_text=row["raw_text"],
            source=row["source_path"],
            line_number=row["line_number"],
            timestamp=row["timestamp"],
            metadata=meta,
            normalized_text=row["normalized_text"]
        )
        deduper.add(rec)

    unique_templates = deduper.get_unique_templates()
    if not unique_templates:
        return {"status": "error", "message": "No unique log templates found"}

    # 5. Embeddings
    self.update_state(state='PROGRESS', meta={'progress': 50, 'status': 'Generating Embeddings'})
    embedder = LocalEmbeddingProvider()
    vectors = embedder.embed(unique_templates)
    
    # Persist vectors to LanceDB (Task 38)
    try:
        groups = deduper.get_all_groups()
        source_list = []
        ts_list = []
        for template in unique_templates:
            records = groups.get(template, [])
            first = records[0] if records else None
            source_list.append(
                first.metadata.get("source_label", first.source if first else "unknown")
                if first
                else "unknown"
            )
            ts_list.append(
                int(first.timestamp.timestamp() * 1000)
                if first and first.timestamp
                else int(time.time() * 1000)
            )

        vector_store.add_embeddings(
            ids=[str(uuid.uuid4()) for _ in unique_templates],
            vectors=vectors,
            templates=unique_templates,
            sources=source_list,
            timestamps=ts_list
        )
    except Exception as e:
        logger.error(f"Failed to persist to LanceDB: {e}")

    # 6. Clustering
    self.update_state(state='PROGRESS', meta={'progress': 60, 'status': 'Clustering Logs'})
    clusterer = LogClusterer()
    clusters = clusterer.fit_predict(
        unique_templates, vectors, deduper.get_all_groups(), deduper.get_all_counts()
    )

    # 7. Anomaly Detection
    anomalies = None
    if baseline:
        bm = BaselineManager(baseline)
        scorer = AnomalyScorer(bm)
        results = scorer.score_batch(unique_templates, vectors)
        anomalies = {res.template: res for res in results}

    # 8. Intelligence
    llm_payload = None
    if intelligence:
        self.update_state(state='PROGRESS', meta={'progress': 80, 'status': 'Generating AI Summary'})
        settings.llm_enabled = True
        intel = IncidentIntelligence()
        llm_payload = intel.generate_summary(clusters, anomalies, top_n=top_n)

    # 9. Format Response
    formatted_clusters = []
    summaries = llm_payload.get("cluster_summaries", []) if llm_payload else []

    for i, c in enumerate(clusters):
        cluster_data = {
            "id": c.cluster_id,
            "cluster_id": c.cluster_id,
            "size": c.size,
            "summary": summaries[i] if i < len(summaries) else "Analyzing...",
            "source": f"{c.representative_source}:{c.representative_line}",
            "representative_log": c.representative_raw,
            "representative_template": c.representative_template,
            "representative_timestamp_ms": getattr(c, "representative_timestamp_ms", 0),
            "anomaly_label": "known",
            "anomaly_score": 0.0
        }
        if anomalies and c.representative_template in anomalies:
            res = anomalies[c.representative_template]
            cluster_data["anomaly_label"] = res.label.value
            cluster_data["anomaly_score"] = res.distance
            
        formatted_clusters.append(cluster_data)

    # 10. Cross-service causal links and severity labels
    try:
        causal_links = CausalScorer().analyze(clusters, deduper.get_all_groups())
    except Exception as e:
        logger.error(f"Causal scorer failed: {e}")
        causal_links = []

    narratives = {}
    if intelligence and causal_links:
        try:
            narratives = IncidentIntelligence().narrate_causal_links(causal_links)
        except Exception as e:
            logger.error(f"Failed to generate causal narration: {e}")

    formatted_links = []
    for link in causal_links:
        key = f"{link.source_service}->{link.target_service}"
        narrative_val = narratives.get(key) or (
            f"Anomalous pattern in {link.source_service} co-occurred with a warning in "
            f"{link.target_service} after an average delay of {link.avg_delay_ms:.1f}ms "
            f"(Confidence: {link.confidence * 100:.0f}%)."
        )
        formatted_links.append({
            "source_cluster_id": link.source_cluster_id,
            "target_cluster_id": link.target_cluster_id,
            "source_service": link.source_service,
            "target_service": link.target_service,
            "source_template": link.source_template,
            "target_template": link.target_template,
            "confidence": link.confidence,
            "avg_delay_ms": link.avg_delay_ms,
            "occurrences": link.occurrences,
            "direction": link.direction,
            "narrative": narrative_val,
        })

    try:
        severity_map = SeverityScorer().score_all(clusters, anomalies, causal_links)
        for cluster_data in formatted_clusters:
            sev = severity_map.get(cluster_data["cluster_id"])
            cluster_data["priority"] = sev.priority if sev else "P3"
            cluster_data["composite_severity_score"] = sev.composite_score if sev else 0.0
            cluster_data["severity_breakdown"] = sev.breakdown if sev else {}
            cluster_data["keyword_flag"] = sev.keyword_flag if sev else False
    except Exception as e:
        logger.error(f"Severity scorer failed: {e}")

    metrics_context = {"status": "disabled", "clusters_correlated": 0, "clusters_total": 0}
    try:
        correlator = MetricsCorrelator()
        clusters_total = 0
        clusters_correlated = 0
        for cluster_data in formatted_clusters:
            if cluster_data.get("cluster_id") == -1:
                cluster_data["metrics_context"] = {"status": "skipped_noise"}
                continue
            clusters_total += 1
            if cluster_data.get("priority", "P3") not in ("P0", "P1", "P2"):
                cluster_data["metrics_context"] = {"status": "skipped_non_incident"}
                continue
            ts_ms = int(cluster_data.get("representative_timestamp_ms") or 0)
            if ts_ms <= 0:
                cluster_data["metrics_context"] = {"status": "no_timestamp"}
                continue
            ctx = correlator.get_context_for_anomaly(ts_ms, window_ms=30000)
            cluster_data["metrics_context"] = ctx
            if ctx.get("status") == "correlated":
                clusters_correlated += 1
        metrics_context = {
            "status": "correlated" if clusters_correlated > 0 else "no_data",
            "clusters_correlated": clusters_correlated,
            "clusters_total": clusters_total,
        }
    except Exception as e:
        logger.error(f"Metrics correlator failed: {e}")
        metrics_context = {"status": "error", "message": str(e)}

    # 11. Database persistence
    self.update_state(state='PROGRESS', meta={'progress': 95, 'status': 'Saving to Database'})
    db = SessionLocal()
    duration = time.time() - start_time
    run_id = self.request.id or f"run_{uuid.uuid4().hex[:8]}"
    source_name = ", ".join(sources)
    try:
        db_run = AnalysisRun(
            id=run_id,
            source=source_name,
            status="Completed",
            raw_lines=deduper.total_count,
            cluster_count=len(clusters),
            reduction_ratio=1.0 - (len(clusters) / deduper.total_count) if deduper.total_count > 0 else 0,
            duration_sec=duration,
            clusters_snapshot=formatted_clusters
        )
        db.add(db_run)
        
        if llm_payload:
            new_incident = Incident(
                title=llm_payload.get("failure_domain", "Unknown Failure"),
                domain=llm_payload.get("failure_domain", "System"),
                impact_score=min(1.0, len(clusters) / 10.0) if len(clusters) > 1 else 0.3,
                summary=llm_payload.get("incident_summary", ""),
                remediation_hints=llm_payload.get("root_cause_hints", []),
                run_id=run_id,
                source=source_name,
                total_logs=deduper.total_count,
                cluster_count=len(clusters),
            )
            db.add(new_incident)
        
        db.commit()
    except Exception as e:
        logger.error(f"DB Error: {e}")
        db.rollback()
    finally:
        db.close()
        
    return {
        "status": "success",
        "run_id": run_id,
        "clusters": formatted_clusters,
        "intelligence": llm_payload,
        "causal_links": formatted_links,
        "metrics_context": metrics_context,
        "total_logs": deduper.total_count,
        "total_logs_analyzed": deduper.total_count,
        "duration_sec": duration,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
