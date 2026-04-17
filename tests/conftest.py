import importlib.util
import sys
import types
import uuid
from pathlib import Path

import pytest
import requests


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXPORTER_PATH = PROJECT_ROOT / "code" / "exporter.py"


@pytest.fixture(autouse=True)
def block_real_network(monkeypatch):
    """Fail fast if any test tries to use real HTTP."""

    def _blocked_request(*args, **kwargs):
        raise AssertionError("Real network calls are disabled in tests. Use mocks.")

    monkeypatch.setattr(requests.sessions.Session, "request", _blocked_request)


@pytest.fixture
def exporter_module(monkeypatch):
    """
    Load exporter.py as a fresh module for each test.

    We set required env vars and provide a minimal prometheus_client stub,
    so tests can import the module without external dependencies.
    """
    monkeypatch.setenv("LOAD_BALANCER_IDS", "123")
    monkeypatch.setenv("ACCESS_TOKEN", "test-token")
    monkeypatch.setenv("SCRAPE_INTERVAL", "30")

    fake_prometheus = types.ModuleType("prometheus_client")

    class _Metric:
        def __init__(self, *args, **kwargs):
            self._args = args
            self._kwargs = kwargs

        def labels(self, **kwargs):
            return self

        def set(self, value):
            return None

        def info(self, value):
            return None

    fake_prometheus.Gauge = _Metric
    fake_prometheus.Info = _Metric
    fake_prometheus.start_http_server = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, "prometheus_client", fake_prometheus)

    module_name = f"exporter_under_test_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, EXPORTER_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module
