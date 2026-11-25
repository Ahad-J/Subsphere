"""Initial SubSphere 4.0 database schema migration.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-11-13
"""
from __future__ import annotations

from app.db.engine import drop_all_tables, init_schema

revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    init_schema()


def downgrade() -> None:
    drop_all_tables()
