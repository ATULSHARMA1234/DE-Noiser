"""
Intelligence module using LLMs to generate incident summaries and root-cause hints.
"""

from __future__ import annotations

import json
import time
from typing import Any

from openai import OpenAI

from denoiser.clustering.models import Cluster
from denoiser.config import settings
from denoiser.detection.models import AnomalyResult
from denoiser.logging import get_logger

logger = get_logger(__name__)

SYSTEM_PROMPT = """
You are an expert Site Reliability Engineer (SRE) and Systems Architect.
Your task is to analyze grouped log clusters and provide a detailed, highly accurate technical incident summary.
Distinguish clearly between "new patterns", "anomalous behavior", and "acknowledged safe events".
Focus on precision, identifying specific affected components, and potential cascading impacts.
"""

def _normalise_payload(payload: Any) -> dict[str, Any]:
    """Coerce the model's JSON into the shapes the rest of the pipeline expects.

    The prompt asks for the failed "component(s)", so a model is within its
    rights to answer with a list — and several do. That list reached
    ``Incident.title``/``Incident.domain``, which are ``String`` columns, and
    psycopg rendered it as a Postgres array literal: the incident list showed
    a title of ``{"Memory Subsystem","Disk I/O Subsystem"}``. Every other
    reader of ``failure_domain`` (Slack, email, the alert router, the run
    detail view) formats it into a message and had the same problem.

    Normalising here rather than at each of those call sites means one rule,
    applied before the value is ever stored.

    Raises ``ValueError`` when the model did not return an object at all. That
    is deliberately noisy: returning ``{}`` instead is falsy, and the pipeline
    reads it as "no intelligence was produced" (`analysis.pipeline`), so the run
    would finish reporting success, create no incident and raise no alert —
    indistinguishable from a run where nothing was wrong. Raising puts it
    through the caller's retry loop and, if the model keeps misbehaving, into
    the heuristic fallback, which is the honest answer.
    """
    if not isinstance(payload, dict):
        raise ValueError(
            f"Expected a JSON object from the model, got {type(payload).__name__}"
        )

    domain = payload.get("failure_domain")
    if isinstance(domain, (list, tuple, set)):
        payload["failure_domain"] = ", ".join(str(d) for d in domain if d) or "System"
    elif domain is not None and not isinstance(domain, str):
        payload["failure_domain"] = str(domain)

    return payload


class IncidentIntelligence:
    """Generates human-readable incident summaries with built-in reliability fallbacks."""

    def __init__(self, enabled: bool | None = None) -> None:
        """``enabled`` overrides the deployment default for this instance only.

        A run that asks for intelligence used to turn it on by assigning
        `settings.llm_enabled = True` — a process-wide singleton, mutated
        per-request and never restored, so one analysis silently enabled the LLM
        for every subsequent run in that worker.
        """
        self.enabled = settings.llm_enabled if enabled is None else enabled
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
        Analyze these clusters in detail:
        {json.dumps(context, indent=2)}

        Provide your response as a JSON object with:
        - "incident_summary": A detailed, comprehensive 4-6 sentence technical summary that explains what went wrong, the potential user/system impact, and the underlying systems involved. Be as specific and accurate as possible.
        - "failure_domain": Likely failed component(s) or subsystems (e.g., "Database Connection Pool", "Payment Gateway").
        - "root_cause_hints": List of 3-5 specific, actionable next steps for investigation.
        - "cluster_summaries": List of accurate summaries (max 15 words) for each cluster explaining its technical relevance. If healthy, return "Executed without error".
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
                return _normalise_payload(json.loads(response.choices[0].message.content))
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

    def narrate_causal_links(
        self,
        links: list[Any],
    ) -> dict[str, str]:
        """Generate a plain-English forensic explanation for each causal link."""
        if not self.enabled or not self.client or not links:
            return {f"{l.source_service}->{l.target_service}": self._local_causal_fallback(l) for l in links}

        narratives = {}
        context = []
        for l in links[:5]:  # Limit to top 5 strongest links to stay within token budgets
            context.append({
                "source_service": l.source_service,
                "target_service": l.target_service,
                "source_template": l.source_template,
                "target_template": l.target_template,
                "avg_delay_ms": l.avg_delay_ms,
                "confidence": l.confidence
            })

        prompt = f"""
        You are an expert SRE and systems architect.
        Analyze these cross-service temporal co-occurrences (causal links):
        {json.dumps(context, indent=2)}

        For each link in the list, provide a clear, concise, plain-English forensic narrative explaining how the source error/event could causally trigger the target warning/error given the delay in milliseconds. Keep each narrative strictly under 30 words.

        Return your response as a JSON object where the keys are EXACTLY "{{source_service}}->{{target_service}}" (with actual service names) and the values are the generated plain-English narratives.
        Example response format:
        {{
           "gateway_service->payment_service": "Database thread exhaustion led to a cascade of gateway connection pool timeouts after 45ms."
        }}
        """

        try:
            logger.info("Requesting LLM causal narration...", extra={"model": self.model})
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You are a professional SRE diagnosing cascading failures across microservices."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.2,
                response_format={"type": "json_object"},
            )
            data = json.loads(response.choices[0].message.content)
            for l in links:
                key = f"{l.source_service}->{l.target_service}"
                narratives[key] = data.get(key, self._local_causal_fallback(l))
            return narratives
        except Exception as e:
            logger.error(f"Failed to generate LLM causal narration: {e}")
            return {f"{l.source_service}->{l.target_service}": self._local_causal_fallback(l) for l in links}

    def _local_causal_fallback(self, link: Any) -> str:
        """Heuristic fallback description for causal links when LLM is offline."""
        return (
            f"Anomalous pattern in {link.source_service} co-occurred with a warning in {link.target_service} "
            f"after an average delay of {link.avg_delay_ms:.1f}ms (Confidence: {link.confidence * 100:.0f}%)."
        )
