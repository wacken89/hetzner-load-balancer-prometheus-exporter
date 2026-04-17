import pytest


def test_open_connections_empty_values_returns_zero(exporter_module, monkeypatch):
    # Arrange: mock metrics response with an empty values list.
    def fake_get_metrics(metric_type, lbid):
        assert metric_type == "open_connections"
        assert lbid == "123"
        return {
            "metrics": {
                "time_series": {
                    "open_connections": {
                        "values": [],
                    }
                }
            }
        }

    monkeypatch.setattr(exporter_module, "get_metrics", fake_get_metrics)

    # Act: this should not raise IndexError even when values is empty.
    payload = exporter_module.get_metrics("open_connections", "123")
    value = exporter_module.extract_latest_metric_value(payload, "open_connections")

    # Assert: exporter falls back to default value instead of crashing.
    assert value == pytest.approx(0.0)


def test_get_metrics_uses_mocked_requests_get(exporter_module, monkeypatch):
    # Arrange: fully mock requests.get so no real HTTP is used.
    captured = {}

    class FakeResponse:
        def __init__(self, data):
            self._data = data
            self.text = str(data)

        def json(self):
            return self._data

    def fake_requests_get(url, headers=None, params=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["params"] = params
        return FakeResponse(
            {
                "metrics": {
                    "time_series": {
                        "open_connections": {"values": [["2026-04-16T00:00:00+00:00", 7]]}
                    }
                }
            }
        )

    monkeypatch.setattr(exporter_module.requests, "get", fake_requests_get)

    # Act
    result = exporter_module.get_metrics("open_connections", "123")
    value = exporter_module.extract_latest_metric_value(result, "open_connections")

    # Assert
    assert value == pytest.approx(7.0)
    assert captured["url"].endswith("/load_balancers/123/metrics")
    assert captured["params"]["type"] == "open_connections"
    assert captured["params"]["step"] == 60
