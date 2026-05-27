"""Low-level Kubernetes apiserver HTTP client.

Mirrors the style of services/docker_http.py — httpx.Client, raise typed
errors for not-found vs other failures, expose a `stream_chunks` iterator
for follow-mode log endpoints. Reuses DockerError/DockerNotFoundError so
callers can catch a single not-found type regardless of backend.
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx

from app.services.docker_http import DockerError, DockerNotFoundError


class KubernetesHttp:
    def __init__(
        self,
        base_url: str,
        token_path: str | None = None,
        ca_path: str | None = None,
        *,
        timeout: float = 30.0,
    ):
        self._token = _read_if_exists(token_path)

        verify: bool | str = True
        if ca_path is not None and ca_path != "":
            verify = ca_path if Path(ca_path).is_file() else False

        self._client = httpx.Client(
            base_url=base_url.rstrip("/") + "/",
            verify=verify,
            timeout=timeout,
        )

    # ---- Basic verbs ----

    def get(self, path: str, query: dict | None = None) -> Any:
        resp = self._client.get(path.lstrip("/"), params=query, headers=self._headers())
        return self._expect_json(resp, path)

    def get_raw(self, path: str, query: dict | None = None) -> str:
        resp = self._client.get(path.lstrip("/"), params=query, headers=self._headers())
        self._assert_ok(resp, path)
        return resp.text

    def post(self, path: str, body: Any) -> Any:
        resp = self._client.post(
            path.lstrip("/"),
            content=json.dumps(body),
            headers={**self._headers(), "Content-Type": "application/json"},
        )
        return self._expect_json(resp, path)

    def put(self, path: str, body: Any) -> Any:
        resp = self._client.put(
            path.lstrip("/"),
            content=json.dumps(body),
            headers={**self._headers(), "Content-Type": "application/json"},
        )
        return self._expect_json(resp, path)

    def patch(self, path: str, body: Any) -> Any:
        """Strategic-merge patch (default for Deployments + the /scale subresource)."""
        resp = self._client.patch(
            path.lstrip("/"),
            content=json.dumps(body),
            headers={
                **self._headers(),
                "Content-Type": "application/strategic-merge-patch+json",
            },
        )
        return self._expect_json(resp, path)

    def delete(self, path: str) -> None:
        resp = self._client.delete(path.lstrip("/"), headers=self._headers())
        if resp.status_code == 404:
            return  # idempotent delete
        self._assert_ok(resp, path)

    def stream_chunks(self, path: str, query: dict | None = None) -> Iterator[bytes]:
        """Open a long-lived response and yield bytes as they arrive.

        Used for pods/log?follow=true. The caller closes the underlying
        response via the returned iterator's .close() (see _ChunkStream).
        """
        req = self._client.build_request(
            "GET",
            path.lstrip("/"),
            params=query,
            headers=self._headers(),
        )
        resp = self._client.send(req, stream=True)
        if resp.status_code == 404:
            resp.close()
            raise DockerNotFoundError(f"Kubernetes resource not found: {path}")
        if resp.status_code >= 400:
            body = resp.read().decode("utf-8", "replace")[:500]
            resp.close()
            raise DockerError(f"Kubernetes API error on {path}: {body or resp.status_code}")
        return _ChunkStream(resp)

    # ---- internals ----

    def _headers(self) -> dict[str, str]:
        h = {"Accept": "application/json"}
        if self._token:
            h["Authorization"] = f"Bearer {self._token}"
        return h

    def _expect_json(self, resp: httpx.Response, path: str) -> Any:
        if resp.status_code == 404:
            raise DockerNotFoundError(f"Kubernetes resource not found: {path}")
        self._assert_ok(resp, path)
        if not resp.content:
            return {}
        try:
            return resp.json()
        except json.JSONDecodeError:
            return {}

    def _assert_ok(self, resp: httpx.Response, path: str) -> None:
        if 200 <= resp.status_code < 300:
            return
        body = resp.text[:500]
        raise DockerError(
            f"Kubernetes API error on {path}: HTTP {resp.status_code} {body}"
        )


class _ChunkStream:
    """httpx stream wrapper exposing the .close() the SSE route expects."""

    def __init__(self, resp: httpx.Response):
        self._resp = resp

    def __iter__(self) -> Iterator[bytes]:
        try:
            yield from self._resp.iter_bytes()
        finally:
            self.close()

    def close(self) -> None:
        try:
            self._resp.close()
        except Exception:  # noqa: BLE001
            pass


def _read_if_exists(path: str | None) -> str | None:
    if not path:
        return None
    p = Path(path)
    if not p.is_file():
        return None
    try:
        return p.read_text().strip()
    except OSError:
        return None
