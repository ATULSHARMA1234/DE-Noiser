"""The model's `failure_domain` reaches String columns, so it has to be a string.

The prompt asks for the failed "component(s)", which invites a JSON array, and
models supply one. `Incident.title` and `Incident.domain` are `String`, so the
list was adapted into a Postgres array literal and the incident list showed
`{"Memory Subsystem","Disk I/O Subsystem"}` where a name belongs. Slack, email
and the alert router all format the same field into a message.
"""

import pytest

from denoiser.intelligence.llm import _normalise_payload


def test_a_list_of_components_becomes_one_readable_string():
    out = _normalise_payload({
        "failure_domain": ["Memory Subsystem", "Disk I/O Subsystem"],
        "incident_summary": "…",
    })
    assert out["failure_domain"] == "Memory Subsystem, Disk I/O Subsystem"


def test_a_string_is_left_exactly_as_it_is():
    out = _normalise_payload({"failure_domain": "Payment Gateway"})
    assert out["failure_domain"] == "Payment Gateway"


def test_an_empty_list_falls_back_rather_than_naming_an_incident_nothing():
    out = _normalise_payload({"failure_domain": []})
    assert out["failure_domain"] == "System"


def test_empty_entries_are_dropped_not_rendered_as_gaps():
    out = _normalise_payload({"failure_domain": ["Database", "", None, "Cache"]})
    assert out["failure_domain"] == "Database, Cache"


def test_a_non_string_scalar_is_coerced():
    out = _normalise_payload({"failure_domain": 42})
    assert out["failure_domain"] == "42"


def test_a_missing_domain_is_left_missing_for_the_caller_default():
    # pipeline/runs supply their own defaults ("Unknown Failure" / "System");
    # inventing one here would mask the difference between "absent" and "empty".
    assert "failure_domain" not in _normalise_payload({"incident_summary": "…"})


def test_a_non_dict_response_is_raised_not_swallowed():
    # Returning `{}` here is the dangerous option: it is falsy, so
    # `analysis.pipeline` reads it as "no intelligence requested", creates no
    # incident and raises no alert, and the run reports success. A malformed
    # model response would then be indistinguishable from a healthy system.
    with pytest.raises(ValueError):
        _normalise_payload(["not", "an", "object"])


def test_a_malformed_response_ends_in_the_heuristic_fallback():
    """The raise has to land somewhere useful, not just somewhere loud."""
    from denoiser.intelligence.llm import IncidentIntelligence

    intelligence = IncidentIntelligence(enabled=False)
    payload = intelligence._generate_local_fallback([])

    # The fallback is what the retry loop reaches after the model keeps
    # answering with something that is not an object. It must be truthy, or the
    # pipeline skips incident creation for exactly the same reason.
    assert payload
    assert isinstance(payload.get("failure_domain"), str)
    assert payload.get("incident_summary")
