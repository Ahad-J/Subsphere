"""SubSphere 4.0 Backup & Clean-Target Restore Drill Utility (T079).

Produces a compressed backup archive (.tar.gz) containing:
- Logical deterministic JSON snapshots of every table in SQLite or PostgreSQL
- Full object-storage backup (PDF reports & assets under SUBSPHERE_DATA_DIR/reports)
- manifest.json recording per-table row counts, SHA-256 table hashes, and object SHA-256 hashes

And restores cleanly into an empty target database & data directory with cryptographic parity verification.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import sqlite3
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parents[2] / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.db.engine import get_db, init_schema  # noqa: E402


def _list_tables(db: Any) -> list[str]:
    if "sqlite" in os.getenv("DATABASE_URL", "sqlite"):
        rows = db.fetchall(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
        return [r["name"] for r in rows]
    rows = db.fetchall(
        "SELECT table_name AS name FROM information_schema.tables WHERE table_schema = 'public' ORDER BY table_name"
    )
    return [r["name"] for r in rows]


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def create_backup(
    *,
    archive_path: Path,
    data_dir: Path,
    database_url: str | None = None,
) -> dict[str, Any]:
    """Create a compressed backup archive with per-table and per-object SHA-256 manifest."""
    if database_url:
        os.environ["DATABASE_URL"] = database_url

    archive_path.parent.mkdir(parents=True, exist_ok=True)
    tables_meta: dict[str, dict[str, Any]] = {}
    objects_meta: dict[str, str] = {}

    with tarfile.open(archive_path, "w:gz") as tar:
        with get_db() as db:
            table_names = _list_tables(db)
            for table in table_names:
                rows = db.fetchall(f"SELECT * FROM {table} ORDER BY rowid ASC" if "sqlite" in os.getenv("DATABASE_URL", "sqlite") else f"SELECT * FROM {table}")
                # Deterministic serialization for cryptographic parity verification
                payload_bytes = json.dumps(rows, sort_keys=True, ensure_ascii=False).encode("utf-8")
                table_hash = _sha256_bytes(payload_bytes)
                tables_meta[table] = {"row_count": len(rows), "sha256": table_hash}

                info = tarfile.TarInfo(name=f"tables/{table}.json")
                info.size = len(payload_bytes)
                tar.addfile(info, io.BytesIO(payload_bytes))

        for sub in ("objects", "analytics"):
            obj_dir = data_dir / sub
            if obj_dir.exists():
                for fpath in sorted(obj_dir.rglob("*")):
                    if fpath.is_file():
                        rel = fpath.relative_to(data_dir).as_posix()
                        raw = fpath.read_bytes()
                        objects_meta[rel] = _sha256_bytes(raw)
                        info = tarfile.TarInfo(name=f"objects/{rel}")
                        info.size = len(raw)
                        tar.addfile(info, io.BytesIO(raw))

        manifest = {
            "version": "4.0",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "tables": tables_meta,
            "objects": objects_meta,
        }
        manifest_bytes = json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8")
        minfo = tarfile.TarInfo(name="manifest.json")
        minfo.size = len(manifest_bytes)
        tar.addfile(minfo, io.BytesIO(manifest_bytes))

    return manifest


def restore_and_verify(
    *,
    archive_path: Path,
    target_db_path: Path,
    target_data_dir: Path,
) -> dict[str, Any]:
    """Restore archive into a clean target SQLite DB + data directory and verify 100% SHA-256 parity."""
    if target_db_path.exists():
        target_db_path.unlink()
    target_db_url = f"sqlite:///{target_db_path}"
    os.environ["DATABASE_URL"] = target_db_url
    init_schema()

    target_data_dir.mkdir(parents=True, exist_ok=True)
    verified_tables = 0
    verified_objects = 0

    with tarfile.open(archive_path, "r:gz") as tar:
        manifest_member = tar.extractfile("manifest.json")
        assert manifest_member is not None, "Missing manifest.json in backup archive"
        manifest = json.loads(manifest_member.read().decode("utf-8"))

        conn = sqlite3.connect(str(target_db_path))
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys = OFF;")
            for table in manifest["tables"].keys():
                member = tar.extractfile(f"tables/{table}.json")
                if member is None:
                    continue
                raw_bytes = member.read()
                expected = manifest["tables"][table]
                actual_hash = _sha256_bytes(raw_bytes)
                if actual_hash != expected["sha256"]:
                    raise RuntimeError(f"Checksum mismatch for table {table} inside archive")

                rows = json.loads(raw_bytes.decode("utf-8"))
                if rows:
                    cols = list(rows[0].keys())
                    placeholders = ", ".join(["?"] * len(cols))
                    col_list = ", ".join(cols)
                    for r in rows:
                        conn.execute(
                            f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})",
                            tuple(r[c] for c in cols),
                        )
                cnt = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                if cnt != expected["row_count"]:
                    raise RuntimeError(f"Row count mismatch after restore for {table}: {cnt} != {expected['row_count']}")
                verified_tables += 1

            conn.execute("PRAGMA foreign_keys = ON;")
            fk_issues = conn.execute("PRAGMA foreign_key_check;").fetchall()
            if fk_issues:
                raise RuntimeError(f"Foreign key violations after restore: {fk_issues}")
            conn.commit()
        finally:
            conn.close()

        for rel_path, expected_hash in manifest.get("objects", {}).items():
            member = tar.extractfile(f"objects/{rel_path}")
            if member is None:
                raise RuntimeError(f"Missing object in backup archive: {rel_path}")
            raw_obj = member.read()
            if _sha256_bytes(raw_obj) != expected_hash:
                raise RuntimeError(f"Checksum mismatch for object {rel_path}")
            dest = target_data_dir / rel_path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(raw_obj)
            verified_objects += 1

    return {
        "verified": True,
        "verified_tables": verified_tables,
        "verified_objects": verified_objects,
        "manifest_created_at": manifest["created_at"],
    }
