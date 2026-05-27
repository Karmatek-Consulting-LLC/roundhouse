"""Low-level Docker Engine HTTP API client.

Accepts either a unix socket path (/var/run/docker.sock) or a tcp:// or
http(s):// endpoint (e.g. docker-socket-proxy). Mirrors only the functions
we need from the Python `docker` SDK so we can stay on httpx and skip the
heavy dependency."""
from __future__ import annotations

import json
import socket
from collections.abc import Iterator
from typing import Any
from urllib.parse import urlencode

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
        # Remember the UDS path so streaming endpoints can bypass httpx for
        # chunked-encoded responses (which httpx buffers until EOF over UDS).
        self._uds_path: str | None = (
            endpoint if not endpoint.startswith(("tcp://", "http://", "https://")) else None
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

    def stream_chunks(self, path: str, query: dict | None = None) -> "_ChunkStream":
        """Yield raw bytes as Docker pushes them. The caller closes when done.

        Over a Unix domain socket we bypass httpx and speak HTTP/1.1 against a
        plain AF_UNIX socket, decoding chunked transfer encoding ourselves.
        httpx's UDS transport buffers chunked streams until EOF, which makes
        `follow=1` log endpoints useless. Over TCP/HTTP we use httpx streaming
        unchanged.
        """
        if self._uds_path:
            return _UdsChunkStream(self._uds_path, path, query, _err_factory=self._to_exception_with_body)
        return _HttpxChunkStream(self._client, path, self._encode_query(query), _err_factory=self._to_exception_with_body)

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


# ---------- Streaming helpers ----------

class _ChunkStream:
    """Common interface for both streaming backends: iterate bytes, then close."""

    def __iter__(self) -> Iterator[bytes]:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError


class _HttpxChunkStream(_ChunkStream):
    """TCP/HTTP path - works correctly with httpx streaming."""

    def __init__(self, client: httpx.Client, path: str, params: dict | None, _err_factory):
        self._req = client.build_request("GET", path.lstrip("/"), params=params)
        self._resp = client.send(self._req, stream=True)
        if self._resp.status_code >= 400:
            body = self._resp.read().decode("utf-8", errors="replace")
            self._resp.close()
            raise _err_factory(self._resp.status_code, body, path)

    def __iter__(self) -> Iterator[bytes]:
        for chunk in self._resp.iter_raw(chunk_size=4096):
            if chunk:
                yield chunk

    def close(self) -> None:
        try:
            self._resp.close()
        except Exception:  # noqa: BLE001
            pass


class _UdsChunkStream(_ChunkStream):
    """UDS path - hand-rolled HTTP/1.1 client. Handles chunked decoding so we
    can yield bytes the moment Docker writes them, instead of waiting for EOF."""

    def __init__(self, uds_path: str, path: str, query: dict | None, _err_factory):
        encoded_query = ""
        if query:
            params: list[tuple[str, str]] = []
            for k, v in query.items():
                if v is None:
                    continue
                if isinstance(v, bool):
                    params.append((k, "1" if v else "0"))
                elif isinstance(v, (list, dict)):
                    params.append((k, json.dumps(v)))
                else:
                    params.append((k, str(v)))
            encoded_query = "?" + urlencode(params)
        request_path = f"/{API_VERSION}/{path.lstrip('/')}{encoded_query}"

        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        # No connect timeout for streaming; the OS will give EHOSTUNREACH fast
        # if the socket path is wrong. Per-recv timeouts are unset → block.
        self._sock.connect(uds_path)

        req = (
            f"GET {request_path} HTTP/1.1\r\n"
            "Host: docker\r\n"
            "Accept: */*\r\n"
            "Connection: close\r\n"
            "\r\n"
        )
        self._sock.sendall(req.encode("ascii"))

        # Read headers, leave any extra bytes as the initial body buffer.
        header_buf = b""
        while b"\r\n\r\n" not in header_buf:
            chunk = self._sock.recv(4096)
            if not chunk:
                self._sock.close()
                raise DockerError(f"Docker closed UDS connection before headers (path={path})")
            header_buf += chunk

        sep = header_buf.index(b"\r\n\r\n")
        headers_blob = header_buf[:sep].decode("iso-8859-1")
        self._body_buf = header_buf[sep + 4 :]

        status_line, _, header_lines = headers_blob.partition("\r\n")
        try:
            status_code = int(status_line.split(" ", 2)[1])
        except (IndexError, ValueError):
            self._sock.close()
            raise DockerError(f"Malformed HTTP status line from Docker: {status_line!r}")

        headers: dict[str, str] = {}
        for line in header_lines.split("\r\n"):
            if ":" in line:
                k, _, v = line.partition(":")
                headers[k.strip().lower()] = v.strip()

        if status_code >= 400:
            # Drain whatever body is left for the error message, then bail.
            try:
                while True:
                    chunk = self._sock.recv(4096)
                    if not chunk:
                        break
                    self._body_buf += chunk
            finally:
                self._sock.close()
            raise _err_factory(status_code, self._body_buf.decode("utf-8", errors="replace"), path)

        self._chunked = headers.get("transfer-encoding", "").lower() == "chunked"

    def __iter__(self) -> Iterator[bytes]:
        if self._chunked:
            yield from self._iter_chunked()
        else:
            yield from self._iter_plain()

    def _iter_plain(self) -> Iterator[bytes]:
        if self._body_buf:
            yield self._body_buf
            self._body_buf = b""
        while True:
            try:
                chunk = self._sock.recv(4096)
            except OSError:
                return
            if not chunk:
                return
            yield chunk

    def _iter_chunked(self) -> Iterator[bytes]:
        buf = self._body_buf
        self._body_buf = b""
        while True:
            # Read the chunk-size line.
            while b"\r\n" not in buf:
                try:
                    extra = self._sock.recv(4096)
                except OSError:
                    return
                if not extra:
                    return
                buf += extra
            size_line, _, buf = buf.partition(b"\r\n")
            # Strip any chunk extensions (e.g. "1f;foo=bar") - rare but legal.
            size_str = size_line.split(b";", 1)[0].strip()
            try:
                size = int(size_str, 16)
            except ValueError:
                return
            if size == 0:
                return  # last chunk
            # Read `size` bytes of payload plus the trailing CRLF.
            while len(buf) < size + 2:
                try:
                    extra = self._sock.recv(4096)
                except OSError:
                    return
                if not extra:
                    return
                buf += extra
            yield buf[:size]
            buf = buf[size + 2 :]

    def close(self) -> None:
        try:
            self._sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self._sock.close()
        except OSError:
            pass


# ---------- Public helper, unchanged from before the streaming refactor ----------

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
