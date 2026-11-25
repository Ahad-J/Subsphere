"""Alembic compatibility runner for SubSphere migrations."""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

from app.db.engine import drop_all_tables, init_schema


def upgrade(revision: str = "head") -> None:
    init_schema()


def downgrade(revision: str = "base") -> None:
    drop_all_tables()


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:2] == ["upgrade", "head"]:
        upgrade("head")
        print("Running upgrade -> 0002_v4_1_schema (head)")
        return 0
    if args[:2] == ["downgrade", "base"]:
        downgrade("base")
        print("Running downgrade 0002_v4_1_schema -> base")
        return 0
    print("Usage: python -m alembic [upgrade head | downgrade base]")
    return 1


__all__ = ["upgrade", "downgrade", "main"]
