"""Vercel Blob over its REST API, for a runner off Vercel that holds a static read-write token.

The wire format follows @vercel/blob 2.8.2 (packages/blob/src at vercel/storage f11f653, API version 13):
put is PUT <api>/?pathname=<p> with x-* option headers; files over MULTIPART_OVER bytes go through
POST <api>/mpu (x-mpu-action create, upload, complete); head and list are GET <api>?url= and GET <api>?prefix=.
The token goes in the authorization header and nowhere else: it is never logged or put in an error message.
"""
import json
import os
import random
import time
import urllib.error
import urllib.parse
import urllib.request

API = os.environ.get("VERCEL_BLOB_API_URL", "https://vercel.com/api/blob")
API_VERSION = "13"
# The docs recommend multipart over 100 MB and document no smaller single-request cap off Vercel.
MULTIPART_OVER = 100 * 1024 * 1024
PART_BYTES = 8 * 1024 * 1024  # the SDK's part size; every part but the last must be at least 5 MiB
RETRY_CODES = {"unknown_error", "service_unavailable", "internal_server_error", "rate_limited"}
TYPES = {".json": "application/json", ".jsonl": "application/x-ndjson", ".mp4": "video/mp4"}


class BlobError(Exception):
    def __init__(self, status, code, message):
        super().__init__(f"blob API {status} {code}: {message}")
        self.status, self.code = status, code


def read_env(path):
    """KEY=VALUE lines (optional `export `, optional single or double quotes, # comments)."""
    env = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.removeprefix("export ").partition("=")
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                value = value[1:-1]
            env[key.strip()] = value
    return env


def set_env(path, key, value):
    """Set one key in the env file in place, so its owner and mode stay as they are."""
    with open(path, "r+") as f:
        lines = f.read().splitlines()
        out, found = [], False
        for line in lines:
            if line.strip().removeprefix("export ").split("=", 1)[0].strip() == key:
                out.append(f'{key}="{value}"')
                found = True
            else:
                out.append(line)
        if not found:
            out.append(f'{key}="{value}"')
        f.seek(0)
        f.write("\n".join(out) + "\n")
        f.truncate()


def content_type(pathname):
    return TYPES.get(os.path.splitext(pathname)[1], "application/octet-stream")


class Blob:
    def __init__(self, token, store_id=None, retries=5, timeout=300):
        if not token:
            raise ValueError("BLOB_READ_WRITE_TOKEN is empty")
        self._token = token
        parts = token.split("_")
        self.store_id = (store_id or (parts[3] if len(parts) > 3 else "")).removeprefix("store_")
        self.retries, self.timeout = retries, timeout

    def _request(self, method, path, query=None, headers=None, body=None):
        url = API + path + ("?" + urllib.parse.urlencode(query) if query else "")
        for attempt in range(self.retries + 1):
            h = {"authorization": f"Bearer {self._token}", "x-api-version": API_VERSION,
                 "x-api-blob-request-attempt": str(attempt),
                 "x-api-blob-request-id": f"{self.store_id}:{int(time.time() * 1000)}:{random.getrandbits(32):08x}",
                 **(headers or {})}
            if self.store_id:
                h["x-vercel-blob-store-id"] = self.store_id
            req = urllib.request.Request(url, data=body, method=method, headers=h)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    text = r.read().decode()
                    return json.loads(text) if text else {}
            except urllib.error.HTTPError as e:
                raw = e.read().decode(errors="replace")
                try:
                    err = json.loads(raw).get("error", {})
                except ValueError:
                    err = {}
                code, message = err.get("code", "unknown_error"), err.get("message", raw[:200])
                if (code in RETRY_CODES or e.code >= 500) and attempt < self.retries:
                    time.sleep(float(e.headers.get("retry-after") or 2 ** attempt))
                    continue
                raise BlobError(e.code, code, message) from None
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                if attempt < self.retries:
                    time.sleep(2 ** attempt)
                    continue
                raise BlobError(0, "network", str(getattr(e, "reason", e))) from None

    @staticmethod
    def _put_headers(ctype, overwrite, max_age):
        h = {"x-vercel-blob-access": "public", "x-content-type": ctype, "x-add-random-suffix": "0",
             "x-allow-overwrite": "1" if overwrite else "0"}
        if max_age is not None:
            h["x-cache-control-max-age"] = str(int(max_age))
        return h

    def put(self, pathname, data, ctype=None, overwrite=False, max_age=None):
        """Upload bytes to pathname. Returns the API's result (url, downloadUrl, pathname, contentType, etag)."""
        ctype = ctype or content_type(pathname)
        headers = self._put_headers(ctype, overwrite, max_age)
        if len(data) > MULTIPART_OVER:
            return self._multipart(pathname, data, headers)
        return self._request("PUT", "/", {"pathname": pathname}, headers, data)

    def _multipart(self, pathname, data, headers, part_bytes=PART_BYTES):
        q = {"pathname": pathname}
        created = self._request("POST", "/mpu", q, {**headers, "x-mpu-action": "create"})
        ids = {"x-mpu-upload-id": created["uploadId"], "x-mpu-key": urllib.parse.quote(created["key"], safe="")}
        parts = []
        for n, start in enumerate(range(0, len(data), part_bytes), 1):
            r = self._request("POST", "/mpu", q, {**headers, **ids, "x-mpu-action": "upload", "x-mpu-part-number": str(n)},
                              data[start:start + part_bytes])
            parts.append({"partNumber": n, "etag": r["etag"]})
        return self._request("POST", "/mpu", q, {**headers, **ids, "x-mpu-action": "complete",
                                                 "content-type": "application/json"}, json.dumps(parts).encode())

    def head(self, pathname):
        """Metadata of one blob (size, uploadedAt, url, cacheControl, etag), or None if there is none."""
        try:
            return self._request("GET", "", {"url": pathname})
        except BlobError as e:
            if e.status == 404 or e.code == "not_found":
                return None
            raise

    def list(self, prefix):
        """Every blob under prefix (dicts with url, pathname, size, uploadedAt)."""
        cursor = None
        while True:
            q = {"prefix": prefix, "limit": "1000", **({"cursor": cursor} if cursor else {})}
            page = self._request("GET", "", q)
            yield from page.get("blobs", [])
            cursor = page.get("cursor")
            if not page.get("hasMore") or not cursor:
                return

    def delete(self, urls):
        if urls:
            self._request("POST", "/delete", None, {"content-type": "application/json"}, json.dumps({"urls": list(urls)}).encode())
