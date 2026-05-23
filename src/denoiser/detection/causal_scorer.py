"""
Causal correlation engine for cross-service observability.

Task 11: Analyzes clustered log events across different services/sources to
identify temporal co-occurrences within a 500ms sliding window. Computes
directed causal links using exponential time-decay metrics and lead-lag
directionality analysis.
"""

from __future__ import annotations

import math
import numpy as np
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, List, Dict, Tuple

from denoiser.clustering.models import Cluster
from denoiser.ingestion.models import LogRecord
from denoiser.logging import get_logger

logger = get_logger(__name__)


@dataclass(slots=True)
class CausalLink:
    """Represents a directed causal link between two log clusters."""
    source_cluster_id: int
    target_cluster_id: int
    source_service: str
    target_service: str
    source_template: str
    target_template: str
    confidence: float  # [0.0 to 1.0]
    avg_delay_ms: float
    occurrences: int
    direction: str  # "A -> B" or "B -> A"


class CausalScorer:
    """High-performance sliding-window causal scorer.

    Analyzes temporal proximity of log cluster occurrences across different sources
    and calculates statistical correlation and lead-lag directions.
    """

    def __init__(self, window_size_ms: float = 500.0, lambda_decay: float = 0.008) -> None:
        """
        Parameters
        ----------
        window_size_ms : float, default 500.0
            Maximum time gap (in ms) to consider two events as temporally correlated.
        lambda_decay : float, default 0.008
            Exponential decay rate for time-proximity scoring.
            With 0.008, e^(-0.008 * 300ms) = 0.09, giving smooth decay within 500ms.
        """
        self.window_size_ms = window_size_ms
        self.lambda_decay = lambda_decay

    def analyze(self, clusters: List[Cluster], template_to_records: Dict[str, List[LogRecord]]) -> List[CausalLink]:
        """Analyze temporal co-occurrences between clusters and construct a causal graph.

        Parameters
        ----------
        clusters : List[Cluster]
            The output clusters from the clustering engine.
        template_to_records : Dict[str, List[LogRecord]]
            Mapping from template string back to the lists of original LogRecords (containing timestamps).

        Returns
        -------
        List[CausalLink]
            A list of validated causal links sorted by confidence (highest first).
        """
        if len(clusters) < 2:
            logger.info("Fewer than 2 clusters found. Skipping causal analysis.")
            return []

        # 1. Flatten all log events into a structured timeline of events
        # We need: timestamp (ms), cluster_id, and service (source_label)
        timeline: List[Dict[str, Any]] = []

        for c in clusters:
            # We skip the noise cluster (-1) in strict causal correlation, 
            # or we can include it. Enterprise standard: keep only valid clusters (>=0)
            # because noise clusters contain unstructured unrelated logs.
            if c.cluster_id < 0:
                continue

            for template in c.templates:
                records = template_to_records.get(template, [])
                for record in records:
                    if record.timestamp is None:
                        continue

                    # Get service name from metadata, fallback to filename stem
                    service = record.metadata.get("source_label", "unknown")
                    
                    # Convert datetime to epoch milliseconds in UTC
                    ts_ms = int(record.timestamp.replace(tzinfo=timezone.utc).timestamp() * 1000)
                    
                    timeline.append({
                        "timestamp": ts_ms,
                        "cluster_id": c.cluster_id,
                        "service": service,
                        "template": template
                    })

        if not timeline:
            logger.info("No log events with valid timestamps found. Causal correlation skipped.")
            return []

        # 2. Sort timeline by timestamp ascending for linear sliding-window parsing
        timeline.sort(key=lambda x: x["timestamp"])

        # 3. High-Performance Sliding Window Co-occurrence Search
        # O(N * K) where N is number of events, K is average events in 500ms window
        # We accumulate interactions between pairs of clusters
        # Key: (cluster_A, cluster_B) -> List[delay_ms (ts_B - ts_A)]
        interactions: Dict[Tuple[int, int], List[float]] = {}
        
        # We also need mappings to retrieve templates/services later
        cluster_info: Dict[int, Tuple[str, str]] = {}
        for c in clusters:
            if c.cluster_id >= 0:
                # Find service name
                service = "unknown"
                for t in c.templates:
                    recs = template_to_records.get(t, [])
                    if recs:
                        service = recs[0].metadata.get("source_label", "unknown")
                        break
                cluster_info[c.cluster_id] = (service, c.representative_template)

        n_events = len(timeline)
        for i in range(n_events):
            event_a = timeline[i]
            ts_a = event_a["timestamp"]
            cid_a = event_a["cluster_id"]
            svc_a = event_a["service"]

            # Slide window forward up to window_size_ms
            for j in range(i + 1, n_events):
                event_b = timeline[j]
                ts_b = event_b["timestamp"]
                cid_b = event_b["cluster_id"]
                svc_b = event_b["service"]

                delay = ts_b - ts_a
                if delay > self.window_size_ms:
                    break  # Out of sliding window

                # Only correlate events across DIFFERENT services
                # Logs within the same service are usually sequence-bound, not cross-service causal
                if cid_a != cid_b and svc_a != svc_b:
                    # Maintain alphabetical/deterministic ordering of cluster pair key
                    pair = (cid_a, cid_b) if cid_a < cid_b else (cid_b, cid_a)
                    
                    # Store signed delay relative to order: ts_b - ts_a is always positive,
                    # so we record direction: if pair is (cid_a, cid_b), delay is (ts_b - ts_a).
                    # If pair is (cid_b, cid_a), delay is -(ts_b - ts_a).
                    signed_delay = delay if cid_a < cid_b else -delay
                    
                    if pair not in interactions:
                        interactions[pair] = []
                    interactions[pair].append(signed_delay)

        # 4. Statistical Scoring and Causal Inference
        causal_links: List[CausalLink] = []

        for (cid_1, cid_2), delays in interactions.items():
            occurrences = len(delays)
            if occurrences < 2:
                # Require at least 2 co-occurrences to establish statistical causality
                # (prevents single accidental co-occurrences from raising alarms)
                continue

            # Separate positive and negative delays to establish lead-lag direction
            pos_delays = [d for d in delays if d > 0]
            neg_delays = [d for d in delays if d < 0]
            zero_delays = [d for d in delays if d == 0]

            # Determine dominant direction
            # If positive delays dominate, cid_1 happens BEFORE cid_2 (cid_1 -> cid_2)
            # If negative delays dominate, cid_2 happens BEFORE cid_1 (cid_2 -> cid_1)
            # In case of exact tie, fallback to order or simultaneous
            pos_count = len(pos_delays) + len(zero_delays) / 2
            neg_count = len(neg_delays) + len(zero_delays) / 2

            if pos_count >= neg_count:
                source_cid, target_cid = cid_1, cid_2
                directional_delays = pos_delays + [0] * len(zero_delays)
                direction_label = f"Cluster {cid_1} -> Cluster {cid_2}"
            else:
                source_cid, target_cid = cid_2, cid_1
                directional_delays = [abs(d) for d in neg_delays] + [0] * len(zero_delays)
                direction_label = f"Cluster {cid_2} -> Cluster {cid_1}"

            if not directional_delays:
                continue

            avg_delay = float(np.mean(directional_delays))
            
            # Proximity score based on exponential decay
            # Smaller delays = higher scores
            proximity_score = math.exp(-self.lambda_decay * avg_delay)

            # Frequency score: we normalize based on logarithmic growth of occurrences
            # 2 occurrences = low confidence, 10+ occurrences = high confidence
            freq_score = min(1.0, math.log(occurrences + 1) / math.log(10))

            # Directional dominance ratio (how clean is the lead-lag offset?)
            dominance_ratio = len(directional_delays) / occurrences

            # Combined causal confidence metric
            confidence = proximity_score * 0.4 + freq_score * 0.4 + dominance_ratio * 0.2

            # Retrieve service names and templates
            svc_src, tmpl_src = cluster_info.get(source_cid, ("unknown", "unknown"))
            svc_tgt, tmpl_tgt = cluster_info.get(target_cid, ("unknown", "unknown"))

            causal_links.append(
                CausalLink(
                    source_cluster_id=source_cid,
                    target_cluster_id=target_cid,
                    source_service=svc_src,
                    target_service=svc_tgt,
                    source_template=tmpl_src,
                    target_template=tmpl_tgt,
                    confidence=round(confidence, 3),
                    avg_delay_ms=round(avg_delay, 2),
                    occurrences=occurrences,
                    direction=direction_label
                )
            )

        # Sort links by confidence in descending order
        return sorted(causal_links, key=lambda link: link.confidence, reverse=True)
