# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Additive migration contract for the custom S3 endpoint setting."""

import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool


def test_migration_adds_nullable_s3_endpoint_without_changing_existing_settings():
    migration_path = (
        Path(__file__).resolve().parents[4]
        / "alembic"
        / "versions"
        / "u1v2w3x4y5z6_add_s3_endpoint_url.py"
    )
    spec = importlib.util.spec_from_file_location("s3_endpoint_migration", migration_path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    db_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    try:
        with db_engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TABLE storageconfiguration ("
                "id INTEGER PRIMARY KEY, provider VARCHAR(32) NOT NULL)"
            )
            connection.exec_driver_sql(
                "INSERT INTO storageconfiguration (id, provider) VALUES (1, 'aws_s3')"
            )
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                migration.upgrade()

            assert connection.execute(
                text("SELECT id, provider, s3_endpoint_url FROM storageconfiguration")
            ).all() == [(1, "aws_s3", None)]
            connection.execute(
                text(
                    "UPDATE storageconfiguration SET s3_endpoint_url = :endpoint WHERE id = 1"
                ),
                {"endpoint": "https://s3.example.com"},
            )
            assert connection.execute(
                text("SELECT s3_endpoint_url FROM storageconfiguration WHERE id = 1")
            ).scalar_one() == "https://s3.example.com"
    finally:
        db_engine.dispose()
