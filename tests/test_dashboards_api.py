"""Dashboards: CRUD, roles, and every widget type's numbers.

The widget data endpoint computes nine widget types from the organisation's own
rows, resolves the UI's type aliases, and honours the time picker. None of it
had a test, which is how the picker shipped moving the control and never the
numbers. Each test seeds incidents in *both* organisations, so a count that
includes the other one fails.
"""

import datetime as dt

import pytest

from denoiser.storage.db import Incident, SessionLocal

NOW = dt.datetime.now(dt.UTC).replace(tzinfo=None)

#: (status, domain, impact, age) for the organisation under test.
MINE = [
    ("OPEN", "Database", 0.9, dt.timedelta(minutes=10)),
    ("OPEN", "Database", 0.7, dt.timedelta(hours=3)),
    ("RESOLVED", "Network", 0.4, dt.timedelta(days=2)),
    ("RESOLVED", "Network", 0.1, dt.timedelta(days=20)),
]


@pytest.fixture
def seeded(two_orgs):
    a, b, as_user = two_orgs
    db = SessionLocal()
    try:
        for status, domain, impact, age in MINE:
            db.add(Incident(tenant_id=a, status=status, domain=domain, impact_score=impact,
                            title=f"{domain} {status}", created_at=NOW - age))
        # Noise in the other organisation that must never show up.
        for _ in range(5):
            db.add(Incident(tenant_id=b, status="OPEN", domain="Other", impact_score=0.95,
                            title="theirs", created_at=NOW - dt.timedelta(minutes=5)))
        db.commit()
    finally:
        db.close()
    return a, b, as_user


def _dashboard(client, *widgets):
    res = client.post("/dashboards", json={
        "name": "Ops",
        "widgets": [{"id": f"w{i}", "type": t, "title": f"W{i}", "config": c} for i, (t, c) in enumerate(widgets)],
    })
    assert res.status_code == 200, res.text
    return res.json()["id"]


def _data(client, dash_id, widget, **params):
    return client.get(f"/dashboards/{dash_id}/widgets/{widget}/data", params=params)


class TestCrud:
    def test_create_read_update_delete(self, two_orgs):
        a, _, as_user = two_orgs
        client = as_user(a)
        dash_id = _dashboard(client, ("metric_card", {"metric": "open_incidents"}))

        assert client.get(f"/dashboards/{dash_id}").json()["name"] == "Ops"
        assert [d["id"] for d in client.get("/dashboards").json()] == [dash_id]

        updated = client.put(f"/dashboards/{dash_id}", json={
            "name": "Renamed", "default_time_range": "24h",
            "widgets": [{"id": "x", "type": "gauge", "title": "G", "config": {}}],
        }).json()
        assert updated["name"] == "Renamed"
        assert updated["default_time_range"] == "24h"
        assert [w["id"] for w in updated["widgets"]] == ["x"]

        assert client.delete(f"/dashboards/{dash_id}").json() == {"status": "deleted"}
        assert client.get(f"/dashboards/{dash_id}").status_code == 404

    def test_partial_update_leaves_other_fields_alone(self, two_orgs):
        a, _, as_user = two_orgs
        client = as_user(a)
        dash_id = _dashboard(client, ("gauge", {}))
        client.put(f"/dashboards/{dash_id}", json={"name": "Only the name"})
        body = client.get(f"/dashboards/{dash_id}").json()
        assert body["name"] == "Only the name" and [w["type"] for w in body["widgets"]] == ["gauge"]

    def test_viewer_reads_but_cannot_write(self, two_orgs):
        a, _, as_user = two_orgs
        dash_id = _dashboard(as_user(a), ("gauge", {}))
        viewer = as_user(a, role="VIEWER")
        assert viewer.get(f"/dashboards/{dash_id}").status_code == 200
        assert viewer.post("/dashboards", json={"name": "x"}).status_code == 403
        assert viewer.put(f"/dashboards/{dash_id}", json={"name": "x"}).status_code == 403
        assert viewer.delete(f"/dashboards/{dash_id}").status_code == 403

    def test_another_organisations_dashboard_is_not_found(self, two_orgs):
        a, b, as_user = two_orgs
        dash_id = _dashboard(as_user(a), ("gauge", {}))
        assert as_user(b).get(f"/dashboards/{dash_id}").status_code == 404
        assert _data(as_user(b), dash_id, "w0").status_code == 404


class TestWidgetData:
    @pytest.mark.parametrize("metric,value", [
        ("open_incidents", 2), ("total_incidents", 4), ("resolved_incidents", 2), ("avg_impact", 0.53),
    ])
    def test_metric_cards_count_only_this_organisation(self, seeded, metric, value):
        a, _, as_user = seeded
        client = as_user(a)
        dash = _dashboard(client, ("metric_card", {"metric": metric}))
        assert _data(client, dash, "w0").json()["value"] == value

    def test_unknown_metric_is_400(self, seeded):
        a, _, as_user = seeded
        client = as_user(a)
        dash = _dashboard(client, ("metric_card", {"metric": "nonsense"}))
        assert _data(client, dash, "w0").status_code == 400

    @pytest.mark.parametrize("window,open_count", [("15m", 1), ("1h", 1), ("4h", 2), ("7d", 2)])
    def test_time_picker_narrows_the_numbers(self, seeded, window, open_count):
        a, _, as_user = seeded
        client = as_user(a)
        dash = _dashboard(client, ("metric_card", {"metric": "open_incidents"}))
        assert _data(client, dash, "w0", start_time=window).json()["value"] == open_count

    def test_absurd_window_is_clamped_not_a_500(self, seeded):
        a, _, as_user = seeded
        client = as_user(a)
        dash = _dashboard(client, ("metric_card", {"metric": "total_incidents"}))
        assert _data(client, dash, "w0", start_time="999999999d").json()["value"] == 4

    def test_time_series_buckets_per_day(self, seeded):
        a, _, as_user = seeded
        client = as_user(a)
        dash = _dashboard(client, ("time_series", {}))
        points = _data(client, dash, "w0").json()["series"][0]["data"]
        assert len(points) == 14
        assert sum(p["value"] for p in points) == 3  # the 20-day-old one is outside the default 14

    def test_bar_groups_by_domain(self, seeded):
        a, _, as_user = seeded
        client = as_user(a)
        dash = _dashboard(client, ("bar", {}))
        assert sorted((b["label"], b["value"]) for b in _data(client, dash, "w0").json()["bars"]) == [
            ("Database", 2), ("Network", 2)]

    def test_gauge_is_the_resolved_ratio(self, seeded):
        a, _, as_user = seeded
        client = as_user(a)
        dash = _dashboard(client, ("gauge", {}))
        body = _data(client, dash, "w0").json()
        assert (body["resolved"], body["total"], body["value"]) == (2, 4, 0.5)

    def test_heatmap_cells_sum_to_the_window(self, seeded):
        a, _, as_user = seeded
        client = as_user(a)
        dash = _dashboard(client, ("heatmap", {"days": 7}))
        body = _data(client, dash, "w0").json()
        assert sum(c["value"] for c in body["cells"]) == 3
        assert all(0 <= c["day"] <= 6 and 0 <= c["hour"] <= 23 for c in body["cells"])

    def test_heatmap_rejects_a_non_integer_window(self, seeded):
        a, _, as_user = seeded
        client = as_user(a)
        dash = _dashboard(client, ("heatmap", {"days": "lots"}))
        assert _data(client, dash, "w0").status_code == 400

    @pytest.mark.parametrize("by,expected", [
        ("status", {"Open": 2, "Resolved": 2}),
        ("domain", {"Database": 2, "Network": 2}),
        ("severity", {"Critical": 1, "High": 1, "Medium": 1, "Low": 1}),
    ])
    def test_pie_slices(self, seeded, by, expected):
        a, _, as_user = seeded
        client = as_user(a)
        dash = _dashboard(client, ("pie_chart", {"by": by}))
        assert {s["name"]: s["value"] for s in _data(client, dash, "w0").json()["slices"]} == expected

    def test_incident_feed_is_newest_first_and_only_ours(self, seeded):
        a, _, as_user = seeded
        client = as_user(a)
        dash = _dashboard(client, ("incident_feed", {}))
        incidents = _data(client, dash, "w0").json()["incidents"]
        assert len(incidents) == 4
        assert incidents[0]["title"] == "Database OPEN" and "theirs" not in {i["title"] for i in incidents}

    def test_markdown_returns_its_own_content(self, seeded):
        a, _, as_user = seeded
        client = as_user(a)
        dash = _dashboard(client, ("markdown", {"content": "# Runbook"}))
        assert _data(client, dash, "w0").json() == {"content": "# Runbook"}

    @pytest.mark.parametrize("alias,key", [
        ("stat", "value"), ("timeseries", "series"), ("logs", "incidents"),
        ("bar_chart", "bars"), ("log_table", "incidents"),
    ])
    def test_ui_type_aliases_resolve(self, seeded, alias, key):
        a, _, as_user = seeded
        client = as_user(a)
        dash = _dashboard(client, (alias, {}))
        assert key in _data(client, dash, "w0").json()

    def test_unknown_widget_type_is_400_and_unknown_widget_404(self, seeded):
        a, _, as_user = seeded
        client = as_user(a)
        dash = _dashboard(client, ("sparkle", {}))
        assert _data(client, dash, "w0").status_code == 400
        assert _data(client, dash, "missing").status_code == 404
