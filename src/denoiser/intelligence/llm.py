"""
Intelligence module using LLMs to generate incident summaries and root-cause hints.
"""

from __future__ import annotations

import json
from typing import Any

from openai import OpenAI

from denoiser.clustering.models import Cluster
from denoiser.config import AnomalyLabel, settings
from denoiser.detection.models import AnomalyResult
from denoiser.logging import get_logger

logger = get_logger(__name__)


class IncidentIntelligence:
    """Generates human-readable incident summaries using an LLM."""

    def __init__(self) -> None:
        self.enabled = settings.llm_enabled
        self.model = settings.llm_model
        
        if self.enabled:
            if not settings.llm_api_key and not settings.llm_base_url:
                logger.warning("LLM is enabled but no API key or base URL is configured.")
                self.enabled = False
            else:
                try:
                    self.client = OpenAI(
                        api_key=settings.llm_api_key or "dummy-key",
                        base_url=settings.llm_base_url,
                    )
                except Exception as e:
                    logger.error(f"Failed to initialize OpenAI client: {e}")
                    self.enabled = False

    def generate_summary(
        self,
        clusters: list[Cluster],
        anomalies: dict[str, AnomalyResult] | None,
        top_n: int = 5,
    ) -> dict[str, Any] | None:
        """Generate a summary, failure-domain classification, and root-cause hints.

        Parameters
        ----------
        clusters : list[Cluster]
            The extracted log clusters.
        anomalies : dict[str, AnomalyResult] | None
            The anomaly results from baseline comparison.
        top_n : int
            Number of top clusters to include in the prompt.

        Returns
        -------
        dict[str, Any] | None
            A structured dictionary containing the LLM's analysis, or None if disabled/failed.
        """
        if not self.enabled:
            return None

        # Prepare context payload for the LLM
        context_data = []
        for c in clusters[:top_n]:
            entry: dict[str, Any] = {
                "cluster_size": c.size,
                "representative_log": c.representative_raw,
                "is_noise_cluster": c.cluster_id == -1,
                "team_label": getattr(c, 'label', None),
                "is_acknowledged": getattr(c, 'is_acknowledged', False),
            }
            if anomalies:
                res = anomalies.get(c.representative_template)
                if res:
                    entry["anomaly_label"] = res.label.value
                    if res.label in (AnomalyLabel.NEW_PATTERN, AnomalyLabel.HIGH_RISK_ANOMALY):
                        entry["is_novel_anomaly"] = True
            
            context_data.append(entry)

        prompt = f"""
        You are an expert Site Reliability Engineer (SRE).
        I am providing you with the top grouped log clusters from a recent system event.
        Analyze these clusters, paying special attention to any marked as "new_pattern" or "high_risk_anomaly".
        
        IMPORTANT: Some clusters have "team_label" and "is_acknowledged" fields. Use these labels to provide 
        organization-specific context. If a cluster is acknowledged, it is a known-safe event.

        Log Clusters:
        {json.dumps(context_data, indent=2)}

        Provide your response as a JSON object with the following keys:
        - "incident_summary": A concise 2-3 sentence summary of what went wrong based on the logs.
        - "failure_domain": The likely component or system that failed (e.g., "Database", "Network", "Authentication").
        - "root_cause_hints": A brief bulleted list of 1-3 likely root causes or investigative next steps.
        - "cluster_summaries": A list of short summaries for each log cluster. IMPORTANT: If the log represents a successful operation, a health check, a debug message, or any "correct" behavior (even if it is a new pattern), you MUST return exactly "Executed without error". If the log represents a failure, exception, crash, or critical error, provide a concise (max 10 words) explanation.
        
        ONLY return valid JSON. Do not include markdown formatting or extra text.
        """

        try:
            logger.info("Requesting incident intelligence summary from LLM", extra={"model": self.model})
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
                response_format={"type": "json_object"},
            )
            
            content = response.choices[0].message.content
            if not content:
                return None
                
            return json.loads(content)
            
        except Exception as e:
            logger.exception("Failed to generate LLM summary")
            return None
