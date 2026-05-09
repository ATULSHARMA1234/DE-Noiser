"""
Intelligence module using LLMs to generate incident summaries and root-cause hints.
"""

from __future__ import annotations

import json
import time
from typing import Any

from openai import OpenAI

from denoiser.clustering.models import Cluster
from denoiser.config import AnomalyLabel, settings
from denoiser.detection.models import AnomalyResult
from denoiser.logging import get_logger

logger = get_logger(__name__)

SYSTEM_PROMPT = """
You are an expert Site Reliability Engineer (SRE). 
Your task is to analyze grouped log clusters and provide a high-level incident summary.
Distinguish between "new patterns" and "acknowledged safe events".
"""

class IncidentIntelligence:
    """Generates human-readable incident summaries with built-in reliability fallbacks."""

    def __init__(self) -> None:
        self.enabled = settings.llm_enabled
        self.model = settings.llm_model
        self.client = None
        
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
        top_n: int = 10,
    ) -> dict[str, Any]:
        """Generate a structured summary using an LLM with fallback logic."""
        if not self.enabled or not self.client:
            return self._generate_local_fallback(clusters)

        # 1. Prepare context
        context = []
        for c in clusters[:top_n]:
            entry = {
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
            context.append(entry)

        prompt = f"""
        Analyze these clusters:
        {json.dumps(context, indent=2)}

        Provide your response as a JSON object with:
        - "incident_summary": 2-3 sentence summary.
        - "failure_domain": Likely failed component (e.g., "Database", "Kernel").
        - "root_cause_hints": List of 1-3 next steps.
        - "cluster_summaries": List of short summaries (max 10 words) for each. If healthy, return "Executed without error".
        """

        # 2. Reliability: Retry logic with backoff
        max_retries = 3
        for attempt in range(max_retries):
            try:
                logger.info(f"LLM analysis attempt {attempt + 1}...", extra={"model": self.model})
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.2,
                    response_format={"type": "json_object"},
                )
                return json.loads(response.choices[0].message.content)
            except Exception as e:
                logger.warning(f"LLM API attempt {attempt + 1} failed: {e}")
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                else:
                    logger.error("All LLM attempts failed. Using local fallback.")
                    return self._generate_local_fallback(clusters)

    def _generate_local_fallback(self, clusters: list[Cluster]) -> dict[str, Any]:
        """Heuristic fallback when AI is unavailable."""
        logger.info("Generating local heuristic fallback summary.")
        total_logs = sum(c.size for c in clusters)
        return {
            "failure_domain": "System / Local Analysis",
            "incident_summary": f"Detected {len(clusters)} distinct log patterns across {total_logs} total events. Local analysis indicates potential pattern shifts.",
            "root_cause_hints": [
                "Inspect clusters with high counts.",
                "Verify API keys for AI-driven root cause analysis.",
                "Check for recent infrastructure changes."
            ],
            "cluster_summaries": ["Pattern detected (Local Heuristic)" for _ in clusters]
        }
