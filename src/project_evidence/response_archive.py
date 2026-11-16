"""Local response bodies for debugging. Request headers are never persisted."""

from datetime import datetime, timezone
import json
from pathlib import Path
import re
from uuid import uuid4

import httpx


MAX_RESPONSE_BYTES = 262_144


class ResponseArchive:
    def __init__(self, directory: Path, api_key: str):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self._api_key = api_key

    def redact(self, text: str) -> str:
        if self._api_key:
            text = text.replace(self._api_key, "[REDACTED_API_KEY]")
            encoded = []
            for character in self._api_key:
                escaped = "\\u" + format(ord(character), "04x")
                encoded.append(f"(?:{re.escape(character)}|{re.escape(escaped)})")
            text = re.sub("".join(encoded), "[REDACTED_API_KEY]", text, flags=re.IGNORECASE)
        return re.sub(r"(?i)bearer\s+[A-Za-z0-9_-]{8,}", "Bearer [REDACTED]", text)

    def save(self, task_id: str, status_code: int, body: bytes, *, truncated=False) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,180}", task_id):
            raise ValueError("Invalid archive task identifier")
        folder = self.directory / task_id
        folder.mkdir(exist_ok=True)
        if folder.is_symlink() or folder.resolve().parent != self.directory:
            raise ValueError("Archive path is outside the configured directory")
        path = folder / f"{uuid4().hex}.json"
        record = {
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "task_id": task_id,
            "http_status": status_code,
            "truncated": truncated,
            "response_body": self.redact(body.decode("utf-8", errors="replace")),
        }
        with path.open("x", encoding="utf-8") as stream:
            json.dump(record, stream, ensure_ascii=False, indent=2)
        return path


class RecordingTransport(httpx.BaseTransport):
    def __init__(self, delegate, archive: ResponseArchive, task_id: str, *, transport_factory=None):
        self.delegate = delegate
        self.archive = archive
        self.task_id = task_id
        self.transport_factory = transport_factory

    def handle_request(self, request):
        delegate = self.transport_factory() if self.transport_factory else self.delegate
        response = None
        body = bytearray()
        truncated = False
        try:
            response = delegate.handle_request(request)
            for chunk in response.iter_bytes():
                available = MAX_RESPONSE_BYTES - len(body)
                body.extend(chunk[:available])
                if len(chunk) > available:
                    truncated = True
                    break
        finally:
            if response is not None:
                response.close()
            if self.transport_factory:
                delegate.close()
        self.archive.save(self.task_id, response.status_code, bytes(body), truncated=truncated)
        if truncated:
            raise ValueError("Provider response exceeded the local response limit")
        headers = {
            key: value for key, value in response.headers.items()
            if key.lower() not in {"content-encoding", "content-length", "transfer-encoding"}
        }
        return httpx.Response(response.status_code, headers=headers, content=bytes(body))
