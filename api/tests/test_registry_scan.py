"""Harbor vulnerability scanning: coordinate mapping, payload normalization,
fetch + cache behavior (httpx.MockTransport — no real Harbor needed)."""
from __future__ import annotations

import json

import httpx
import pytest

from app.services import registry_scan
from app.services.registry_scan import (
    HarborScanner,
    VulnSummary,
    _normalize,
    harbor_coordinates,
)


# ---- coordinate mapping ----

def test_coordinates_simple_prefix():
    c = harbor_coordinates("harbor.example.com/roundhouse", "crew")
    assert c is not None
    assert c.api_base == "https://harbor.example.com/api/v2.0"
    assert c.ui_base == "https://harbor.example.com"
    assert c.project == "roundhouse"
    assert c.repository == "mcp-server-crew"


def test_coordinates_nested_prefix_and_port():
    c = harbor_coordinates("harbor.local:8443/team/mcp", "crew")
    assert c is not None
    assert c.api_base == "https://harbor.local:8443/api/v2.0"
    assert c.project == "team"
    assert c.repository == "mcp/mcp-server-crew"


def test_coordinates_match_pushed_image_name():
    """The repository the scanner queries must be the one deploys push,
    or every lookup 404s as 'Image not found in registry'."""
    from app.services.docker import image_tag

    prefix = "harbor.example.com/roundhouse"
    c = harbor_coordinates(prefix, "crew")
    assert image_tag("crew", prefix) == f"harbor.example.com/{c.project}/{c.repository}:latest"


def test_coordinates_require_project_path():
    assert harbor_coordinates("registry.local:5000", "crew") is None
    assert harbor_coordinates("", "crew") is None


def test_coordinates_api_override_drives_ui_base():
    c = harbor_coordinates("harbor.internal/proj", "crew", "http://harbor-api.dmz/api/v2.0")
    assert c is not None
    assert c.api_base == "http://harbor-api.dmz/api/v2.0"
    assert c.ui_base == "http://harbor-api.dmz"


# ---- normalization ----

def _harbor_artifact(status="Success", total=7, fixable=3, sev="Critical",
                     counts=None):
    return {
        "digest": "sha256:abc",
        "scan_overview": {
            "application/vnd.security.vulnerability.report; version=1.1": {
                "scan_status": status,
                "severity": sev,
                "end_time": "2026-07-11T02:00:00Z",
                "summary": {
                    "total": total,
                    "fixable": fixable,
                    "summary": counts if counts is not None else {"Critical": 2, "High": 5},
                },
            }
        },
    }


def test_normalize_vulnerable():
    s = _normalize(_harbor_artifact(), "https://h/link")
    assert s.status == "vulnerable"
    assert s.severity == "Critical"
    assert s.total == 7 and s.fixable == 3
    assert s.by_severity == {"Critical": 2, "High": 5}
    assert s.scanned_at == "2026-07-11T02:00:00Z"
    assert s.report_url == "https://h/link"


def test_normalize_clean():
    s = _normalize(_harbor_artifact(total=0, fixable=0, sev="None", counts={}), None)
    assert s.status == "clean" and s.total == 0 and s.by_severity == {}


def test_normalize_running_and_missing():
    assert _normalize(_harbor_artifact(status="Running"), None).status == "scanning"
    assert _normalize({"digest": "sha256:abc"}, None).status == "unscanned"
    assert _normalize(_harbor_artifact(status="Error"), None).status == "error"


# ---- fetch + cache (MockTransport) ----

@pytest.fixture()
def scanner_with_mock(monkeypatch):
    calls = {"artifact": 0, "project": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/artifacts/latest"):
            calls["artifact"] += 1
            assert request.url.params["with_scan_overview"] == "true"
            return httpx.Response(200, json=_harbor_artifact())
        if path.endswith("/projects/roundhouse"):
            calls["project"] += 1
            return httpx.Response(200, json={"project_id": 42})
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    real_client = httpx.Client
    monkeypatch.setattr(
        registry_scan.httpx, "Client",
        lambda **kw: real_client(transport=transport),
    )
    scanner = HarborScanner()
    monkeypatch.setattr(
        scanner, "_config",
        lambda db: ("harbor.example.com/roundhouse", "", ("robot$ci", "pw"), None),
    )
    return scanner, calls


def test_summaries_fetch_normalize_and_deep_link(scanner_with_mock):
    scanner, calls = scanner_with_mock
    out = scanner.summaries(None, ["crew"])
    s = out["crew"]
    assert s["status"] == "vulnerable" and s["total"] == 7
    assert s["report_url"] == "https://harbor.example.com/harbor/projects/42/repositories/mcp-server-crew"
    assert calls["artifact"] == 1


def test_summaries_cached_within_ttl(scanner_with_mock):
    scanner, calls = scanner_with_mock
    scanner.summaries(None, ["crew"])
    scanner.summaries(None, ["crew"])
    assert calls["artifact"] == 1  # second render served from cache
    scanner.invalidate("crew")
    scanner.summaries(None, ["crew"])
    assert calls["artifact"] == 2


def test_unreachable_harbor_degrades_to_error(monkeypatch):
    real_client = httpx.Client
    transport = httpx.MockTransport(lambda req: (_ for _ in ()).throw(httpx.ConnectError("nope")))
    monkeypatch.setattr(registry_scan.httpx, "Client", lambda **kw: real_client(transport=transport))
    scanner = HarborScanner()
    monkeypatch.setattr(
        scanner, "_config", lambda db: ("harbor.example.com/roundhouse", "", None, None)
    )
    out = scanner.summaries(None, ["crew"])
    assert out["crew"]["status"] == "error"
    assert "Cannot reach" in out["crew"]["detail"]


def test_bad_credentials_get_actionable_detail(monkeypatch):
    real_client = httpx.Client
    transport = httpx.MockTransport(lambda req: httpx.Response(403, json={}))
    monkeypatch.setattr(registry_scan.httpx, "Client", lambda **kw: real_client(transport=transport))
    scanner = HarborScanner()
    monkeypatch.setattr(
        scanner, "_config", lambda db: ("harbor.example.com/roundhouse", "", None, None)
    )
    out = scanner.summaries(None, ["crew"])
    assert out["crew"]["status"] == "error"
    assert "robot account" in out["crew"]["detail"]


def test_no_config_reports_unsupported(monkeypatch):
    scanner = HarborScanner()
    monkeypatch.setattr(scanner, "_config", lambda db: None)
    out = scanner.summaries(None, ["a", "b"])
    assert all(v["status"] == "unsupported" for v in out.values())


def test_vuln_summary_roundtrips_to_dict():
    d = VulnSummary(status="clean").to_dict()
    assert json.dumps(d)  # JSON-serializable
    assert d["status"] == "clean" and d["by_severity"] == {}
