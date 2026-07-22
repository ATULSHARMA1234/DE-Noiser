"""
Tests for resolving which service a log came from.

Reading only a bare "source" key meant logs from every real shipper landed as
"unknown". That is not a cosmetic loss: per-service grouping is what topology,
causal correlation and cross-service spike detection operate on, so an ingest
that drops it produces a system that clusters fine and correlates nothing.
"""

import pytest

from denoiser.storage.clickhouse_store import UNKNOWN_SOURCE, resolve_source


class TestFlatKeys:
    @pytest.mark.parametrize(
        "log",
        [
            {"source": "checkout"},
            {"service": "checkout"},
            {"service_name": "checkout"},
            {"service.name": "checkout"},
            {"container_name": "checkout"},
            {"app": "checkout"},
            {"logger_name": "checkout"},
        ],
    )
    def test_each_supported_key_resolves(self, log):
        assert resolve_source(log) == "checkout"


class TestNestedPaths:
    def test_fluent_bit_kubernetes_shape(self):
        """Fluent Bit's kubernetes filter nests the container name."""
        log = {"log": "boom", "kubernetes": {"container_name": "checkout", "namespace_name": "prod"}}

        assert resolve_source(log) == "checkout"

    def test_kubernetes_app_label(self):
        log = {"kubernetes": {"labels": {"app": "payment"}}}

        assert resolve_source(log) == "payment"

    def test_otel_dotted_key_when_flattened(self):
        """OTel exporters may flatten service.name into a literal dotted key."""
        assert resolve_source({"service.name": "auth"}) == "auth"

    def test_otel_dotted_key_when_nested(self):
        """...or nest it under service."""
        assert resolve_source({"service": {"name": "auth"}}) == "auth"


class TestPriority:
    def test_explicit_source_wins(self):
        log = {"source": "explicit", "service": "secondary", "app": "tertiary"}

        assert resolve_source(log) == "explicit"

    def test_service_beats_app(self):
        assert resolve_source({"app": "tertiary", "service": "secondary"}) == "secondary"


class TestFallback:
    def test_no_identifying_field_is_unknown(self):
        assert resolve_source({"message": "hello", "level": "INFO"}) == UNKNOWN_SOURCE

    def test_empty_string_does_not_count(self):
        """An empty service name must fall through, not become the source."""
        assert resolve_source({"service": "   ", "app": "real-name"}) == "real-name"

    def test_empty_log_is_unknown(self):
        assert resolve_source({}) == UNKNOWN_SOURCE

    def test_non_string_scalar_is_accepted(self):
        """A numeric service id is a worse name than a string, but better than nothing."""
        assert resolve_source({"service": 42}) == "42"

    def test_structural_values_are_skipped(self):
        """A dict at 'service' is structure; only its nested name should be used."""
        assert resolve_source({"service": {"unrelated": "x"}, "app": "real-name"}) == "real-name"

    def test_list_value_is_skipped(self):
        assert resolve_source({"service": ["a", "b"], "app": "real-name"}) == "real-name"


class TestWhitespace:
    def test_value_is_trimmed(self):
        assert resolve_source({"service": "  checkout  "}) == "checkout"
