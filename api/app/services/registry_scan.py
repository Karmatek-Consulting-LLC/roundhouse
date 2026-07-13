"""Registry vulnerability scanning (Harbor/Trivy).

Harbor scans pushed images with Trivy and exposes per-artifact scan overviews
on its REST API. Roundhouse pushes every server image as
`{registry_prefix}/mcp-server-{name}:latest` (see `docker.image_repo_name`),
so the configured registry prefix yields the API coordinates directly: the
prefix host becomes the API base (`https://{host}/api/v2.0` unless
overridden), the first path segment is the Harbor project, and the rest +
`mcp-server-{name}` is the repository. The registry
credentials already stored in Platform Settings (a Harbor robot account)
authenticate the API call — grant the robot scan-read on the project.

Results are cached in-process with a short TTL so rendering the servers list
doesn't hammer Harbor; failures are cached briefly too so an unreachable
Harbor degrades to a gray badge instead of slow page loads. The provider
surface is intentionally small (enabled / summaries) so other registries'
scan APIs (Quay, ACR, GHCR) can slot in later.
"""
from __future__ import annotations

import concurrent.futures
import logging
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import quote

import httpx
from sqlalchemy.orm import Session

from app.platform_settings import (
    SETTING_CUSTOM_CA_CERT,
    SETTING_REGISTRY_SCANNER,
    SETTING_REGISTRY_SCANNER_API_URL,
    get_setting,
)
from app.services.docker import image_repo_name

logger = logging.getLogger(__name__)

SCANNER_HARBOR = "harbor"

# Severity display order; Harbor/Trivy severity strings map onto these.
SEVERITIES = ("Critical", "High", "Medium", "Low", "Unknown")

_CACHE_TTL_OK = 300.0  # scans change on push, not per page render
_CACHE_TTL_ERR = 60.0  # retry an unreachable Harbor soon, but not per render
_FETCH_TIMEOUT = httpx.Timeout(5.0, connect=3.0)


@dataclass
class VulnSummary:
    # "clean" | "vulnerable" | "scanning" | "unscanned" | "unsupported" | "error"
    status: str
    severity: str | None = None  # Harbor's overall severity, e.g. "Critical"
    total: int = 0
    fixable: int = 0
    by_severity: dict[str, int] = field(default_factory=dict)
    scanned_at: str | None = None
    report_url: str | None = None
    detail: str | None = None  # human-readable note for error/unsupported

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "severity": self.severity,
            "total": self.total,
            "fixable": self.fixable,
            "by_severity": self.by_severity,
            "scanned_at": self.scanned_at,
            "report_url": self.report_url,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class HarborCoordinates:
    api_base: str  # https://host/api/v2.0 (no trailing slash)
    ui_base: str  # https://host
    project: str
    repository: str  # may contain "/" for nested prefixes


def harbor_coordinates(
    registry_prefix: str, server_name: str, api_url_override: str = ""
) -> HarborCoordinates | None:
    """Map the docker registry prefix + server name onto Harbor API coordinates.
    Returns None when the prefix has no project path (Harbor requires one)."""
    prefix = (registry_prefix or "").strip().strip("/")
    if not prefix or "/" not in prefix:
        return None
    host, path = prefix.split("/", 1)
    project, _, rest = path.partition("/")
    if not project:
        return None
    image = image_repo_name(server_name)
    repository = f"{rest}/{image}" if rest else image
    override = (api_url_override or "").strip().rstrip("/")
    api_base = override or f"https://{host}/api/v2.0"
    # UI base tracks the API host (the override may route through a different
    # ingress than docker pulls do).
    ui_base = api_base[: -len("/api/v2.0")] if api_base.endswith("/api/v2.0") else f"https://{host}"
    return HarborCoordinates(api_base=api_base, ui_base=ui_base, project=project, repository=repository)


def enabled(db: Session) -> bool:
    if (get_setting(db, SETTING_REGISTRY_SCANNER, "") or "").strip() != SCANNER_HARBOR:
        return False
    from app.services.server_service import get_server_service

    return bool(get_server_service().registry_prefix(db))


class HarborScanner:
    """Fetch + normalize + cache Harbor scan overviews."""

    def __init__(self):
        self._cache: dict[str, tuple[float, VulnSummary]] = {}
        self._project_ids: dict[str, int] = {}  # api_base|project -> numeric id
        self._lock = threading.Lock()

    # ---- public ----

    def summaries(self, db: Session, names: list[str]) -> dict[str, dict]:
        """Vulnerability summaries for `names`, fanned out concurrently on
        cache misses. Never raises: per-server failures come back as summaries
        with status 'error'."""
        cfg = self._config(db)
        if cfg is None:
            return {n: VulnSummary(status="unsupported", detail="Scanner not configured").to_dict() for n in names}
        prefix, api_override, auth, verify = cfg

        now = time.time()
        out: dict[str, dict] = {}
        missing: list[str] = []
        with self._lock:
            for n in names:
                hit = self._cache.get(n)
                if hit and hit[0] > now:
                    out[n] = hit[1].to_dict()
                else:
                    missing.append(n)
        if missing:
            with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(missing))) as ex:
                fetched = list(
                    ex.map(lambda n: self._fetch_one(n, prefix, api_override, auth, verify), missing)
                )
            with self._lock:
                for n, summary in zip(missing, fetched):
                    ttl = _CACHE_TTL_ERR if summary.status == "error" else _CACHE_TTL_OK
                    self._cache[n] = (time.time() + ttl, summary)
                    out[n] = summary.to_dict()
        return out

    def invalidate(self, server_name: str | None = None) -> None:
        with self._lock:
            if server_name is None:
                self._cache.clear()
            else:
                self._cache.pop(server_name, None)

    # ---- internals ----

    def _config(self, db: Session):
        from app.services.mcp_client import verify_for_ca
        from app.services.server_service import get_server_service

        if not enabled(db):
            return None
        service = get_server_service()
        prefix = service.registry_prefix(db) or ""
        api_override = (get_setting(db, SETTING_REGISTRY_SCANNER_API_URL, "") or "").strip()
        auth_cfg = service.registry_auth(db)
        auth = (auth_cfg["username"], auth_cfg["password"]) if auth_cfg else None
        try:
            verify = verify_for_ca(get_setting(db, SETTING_CUSTOM_CA_CERT, "") or "")
        except Exception:  # noqa: BLE001 - a bad CA shouldn't break badges
            verify = None
        return prefix, api_override, auth, verify

    def _fetch_one(self, name, prefix, api_override, auth, verify) -> VulnSummary:
        coords = harbor_coordinates(prefix, name, api_override)
        if coords is None:
            return VulnSummary(
                status="unsupported",
                detail="Registry prefix has no project path (expected host/project)",
            )
        repo_enc = quote(quote(coords.repository, safe=""), safe="")  # Harbor double-encodes "/"
        url = (
            f"{coords.api_base}/projects/{quote(coords.project, safe='')}"
            f"/repositories/{repo_enc}/artifacts/latest"
        )
        try:
            with httpx.Client(timeout=_FETCH_TIMEOUT, verify=verify if verify is not None else True) as client:
                resp = client.get(
                    url,
                    params={"with_scan_overview": "true"},
                    auth=auth,
                    headers={"Accept": "application/json"},
                )
                if resp.status_code == 404:
                    return VulnSummary(status="unscanned", detail="Image not found in registry")
                if resp.status_code in (401, 403):
                    return VulnSummary(
                        status="error",
                        detail="Registry credentials were rejected by the Harbor API "
                        "(grant the robot account scan/artifact read permission)",
                    )
                if resp.status_code >= 400:
                    return VulnSummary(status="error", detail=f"Harbor API returned HTTP {resp.status_code}")
                artifact = resp.json()
                report_url = self._report_url(client, coords, auth)
        except httpx.HTTPError as e:
            return VulnSummary(status="error", detail=f"Cannot reach Harbor API: {e}")
        except ValueError:
            return VulnSummary(status="error", detail="Harbor API returned non-JSON response")
        return _normalize(artifact, report_url)

    def _report_url(self, client: httpx.Client, coords: HarborCoordinates, auth) -> str | None:
        """Deep link to the repository in Harbor's UI (needs the numeric
        project id; looked up once per project and memoized)."""
        key = f"{coords.api_base}|{coords.project}"
        with self._lock:
            pid = self._project_ids.get(key)
        if pid is None:
            try:
                resp = client.get(
                    f"{coords.api_base}/projects/{quote(coords.project, safe='')}", auth=auth
                )
                if resp.status_code >= 400:
                    return None
                pid = int(resp.json().get("project_id"))
            except (httpx.HTTPError, ValueError, TypeError):
                return None
            with self._lock:
                self._project_ids[key] = pid
        repo_enc = quote(coords.repository, safe="")
        return f"{coords.ui_base}/harbor/projects/{pid}/repositories/{repo_enc}"


def _normalize(artifact: dict, report_url: str | None) -> VulnSummary:
    """Collapse Harbor's artifact + scan_overview shape into a VulnSummary."""
    overview = artifact.get("scan_overview") or {}
    # scan_overview is keyed by report MIME type; take the first entry.
    report = next(iter(overview.values()), None) if isinstance(overview, dict) else None
    if not isinstance(report, dict):
        return VulnSummary(status="unscanned", report_url=report_url, detail="No scan report yet")

    scan_status = str(report.get("scan_status") or "").lower()
    if scan_status in ("running", "pending", "scheduled"):
        return VulnSummary(status="scanning", report_url=report_url)
    if scan_status and scan_status not in ("success",):
        return VulnSummary(status="error", report_url=report_url, detail=f"Scan status: {scan_status}")

    summary = report.get("summary") or {}
    counts_raw = summary.get("summary") or {}
    by_severity = {s: int(counts_raw.get(s, 0) or 0) for s in SEVERITIES if counts_raw.get(s)}
    total = int(summary.get("total", 0) or 0)
    return VulnSummary(
        status="vulnerable" if total > 0 else "clean",
        severity=report.get("severity") or None,
        total=total,
        fixable=int(summary.get("fixable", 0) or 0),
        by_severity=by_severity,
        scanned_at=report.get("end_time") or None,
        report_url=report_url,
    )


_scanner: HarborScanner | None = None


def get_scanner() -> HarborScanner:
    global _scanner
    if _scanner is None:
        _scanner = HarborScanner()
    return _scanner
