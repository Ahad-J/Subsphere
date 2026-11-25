"""Private object storage adapter (MinIO S3 compatible + persistent local private store)."""
from __future__ import annotations

import os
from pathlib import Path

from app.core.config import get_settings


class PrivateObjectStore:
    def __init__(self, base_dir: str | None = None):
        settings = get_settings()
        self.base_dir = Path(base_dir or settings.data_dir) / "objects"
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.minio_endpoint = settings.minio_endpoint

    def put_bytes(self, object_key: str, data: bytes, content_type: str = "application/pdf") -> str:
        safe_key = object_key.lstrip("/").replace("..", "_")
        target = self.base_dir / safe_key
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return safe_key

    def get_bytes(self, object_key: str) -> bytes | None:
        safe_key = object_key.lstrip("/").replace("..", "_")
        target = self.base_dir / safe_key
        if not target.exists():
            return None
        return target.read_bytes()

    def exists(self, object_key: str) -> bool:
        safe_key = object_key.lstrip("/").replace("..", "_")
        return (self.base_dir / safe_key).exists()
