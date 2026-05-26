"""Low-level Docker Engine HTTP API client.

Accepts either a unix socket path (/var/run/docker.sock) or a tcp:// or
http(s):// endpoint (e.g. docker-socket-proxy). Mirrors only the functions
we need from the Python `docker` SDK so we can stay on httpx and skip the
heavy dependency."""
from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import httpx


API_VERSION = "v1.43"


class DockerError(RuntimeError):
    """Base Docker API error."""


class DockerNotFoundError(DockerError):
    """The Docker resource does not exist."""


def _to_url(endpoint: str) -> tuple[str, httpx.BaseTransport | None]:
    """Build the base URL + transport tuple from a docker host string."""
    if endpoint.startswith(("tcp://", "http://", "https://")):
        base = endpoint
        if endpoint.startswith("tcp://"):
            base = "http://" + endpoint[len("tcp://") :]
        return base.rstrip("/") + f"/{API_VERSION}/", None
    # Unix socket
    return f"http://docker/{API_VERSION}/", httpx.HTTPTransport(uds=endpoint)


class DockerHttp:
    def __init__(self, endpoint: str, *, timeout: float = 120.0):
        base_url, transport = _to_url(endpoint)
        self._client = httpx.Client(
            base_url=base_url,
            timeout=timeout,
            transport=transport,
        )

    # ---- Basic verbs ----

    def get(self, path: str, query: dict | None = None) -> Any:
        resp = self._client.get(path.lstrip("/"), params=self._encode_query(query))
        return self._expect_json(resp, path)

    def post(
        self,
        path: str,
        query: dict | None = None,
        body: Any | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        kwargs: dict[str, Any] = {"params": self._encode_query(query)}
        if body is not None:
            kwargs["json"] = body
        if headers:
            kwargs["headers"] = headers
        resp = self._client.post(path.lstrip("/"), **kwargs)
        if resp.status_code >= 400:
            raise self._to_exception(resp, path)
        if not resp.content:
            return {}
        try:
            return resp.json()
        except json.JSONDecodeError:
            return {}

    def delete(self, path: str, query: dict | None = None) -> None:
        resp = self._client.delete(path.lstrip("/"), params=self._encode_query(query))
        if resp.status_code >= 400 and resp.status_code != 404:
            raise self._to_exception(resp, path)

    def post_stream(
        self,
        path: str,
        query: dict | None,
        body: bytes | None,
        headers: dict[str, str] | None = None,
    ) -> Iterator[dict]:
        """POST and yield NDJSON frames from the response body (image build/push)."""
        resp = self._client.post(
            path.lstrip("/"),
            params=self._encode_query(query),
            content=body,
            headers=headers,
            timeout=600,
        )
        if resp.status_code >= 400:
            raise self._to_exception(resp, path)
        for line in resp.text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                frame = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(frame, dict):
                yield frame

    def get_raw(self, path: str, query: dict | None = None) -> bytes:
        resp = self._client.get(path.lstrip("/"), params=self._encode_query(query))
        if resp.status_code >= 400:
            raise self._to_exception(resp, path)
        return resp.content

    def stream_raw(self, path: str, query: dict | None = None):
        """Open a long-lived GET that yields bytes as Docker pushes them.
        Used by the log-follow endpoint. The caller is responsible for
        breaking out of the iterator when it wants to stop."""
        req = self._client.build_request("GET", path.lstrip("/"), params=self._encode_query(query))
        resp = self._client.send(req, stream=True)
        if resp.status_code >= 400:
            # Drain so we can show the error message.
            body = resp.read().decode("utf-8", errors="replace")
            resp.close()
            raise self._to_exception_with_body(resp.status_code, body, path)
        return resp

    @staticmethod
    def _to_exception_with_body(status_code: int, body: str, path: str) -> DockerError:
        message = body
        try:
            decoded = json.loads(body)
            if isinstance(decoded, dict) and "message" in decoded:
                message = str(decoded["message"])
        except json.JSONDecodeError:
            pass
        if status_code == 404:
            return DockerNotFoundError(f"Not found: {path} - {message}")
        return DockerError(f"Docker API {status_code} on {path}: {message}")

    # ---- Helpers ----

    @staticmethod
    def _encode_query(query: dict | None) -> dict[str, str] | None:
        if not query:
            return None
        out: dict[str, str] = {}
        for k, v in query.items():
            if v is None:
                continue
            if isinstance(v, bool):
                out[k] = "1" if v else "0"
            elif isinstance(v, (list, dict)):
                out[k] = json.dumps(v)
            else:
                out[k] = str(v)
        return out

    @staticmethod
    def _expect_json(resp: httpx.Response, path: str) -> Any:
        if resp.status_code == 404:
            raise DockerNotFoundError(f"Not found: {path}")
        if resp.status_code >= 400:
            raise DockerHttp._to_exception(resp, path)
        if not resp.content:
            return {}
        try:
            return resp.json()
        except json.JSONDecodeError:
            return {}

    @staticmethod
    def _to_exception(resp: httpx.Response, path: str) -> DockerError:
        body = resp.text
        message = body
        try:
            decoded = resp.json()
            if isinstance(decoded, dict) and "message" in decoded:
                message = str(decoded["message"])
        except json.JSONDecodeError:
            pass
        if resp.status_code == 404:
            return DockerNotFoundError(f"Not found: {path} - {message}")
        return DockerError(f"Docker API {resp.status_code} on {path}: {message}")


def demux_log_frames(raw: bytes) -> str:
    """Demultiplex Docker stdout/stderr log frames (TTY-disabled streams).

    Frame format: 1 byte stream (1=stdout, 2=stderr), 3 bytes padding, 4 bytes
    big-endian size, then `size` bytes of payload. Concatenate all payloads.
    Falls back to returning the raw bytes if it doesn't look multiplexed."""
    out: list[bytes] = []
    pos = 0
    n = len(raw)
    while pos + 8 <= n:
        stream = raw[pos]
        size = int.from_bytes(raw[pos + 4 : pos + 8], "big")
        if stream > 2:
            return raw.decode("utf-8", errors="replace")
        pos += 8
        if pos + size > n:
            out.append(raw[pos:])
            break
        out.append(raw[pos : pos + size])
        pos += size
    return b"".join(out).decode("utf-8", errors="replace")
