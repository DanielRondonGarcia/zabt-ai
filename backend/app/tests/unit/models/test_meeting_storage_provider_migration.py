# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Migration contract for existing and new meeting storage-provider values."""

import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import StaticPool


def test_migration_backfills_existing_meetings_and_restricts_provider_values():
    migration_path = (
        Path(__file__).resolve().parents[4]
        / "alembic"
        / "versions"
        / "t0u1v2w3x4y5_add_meeting_storage_provider.py"
    )
    spec = importlib.util.spec_from_file_location(
        "meeting_storage_provider_migration", migration_path
    )
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
                "CREATE TABLE meeting (id INTEGER PRIMARY KEY, title VARCHAR(255) NOT NULL)"
            )
            connection.exec_driver_sql(
                "INSERT INTO meeting (id, title) VALUES (1, 'existing')"
            )
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                migration.upgrade()

            assert connection.execute(
                text("SELECT id, storage_provider FROM meeting ORDER BY id")
            ).all() == [(1, "minio")]

            connection.exec_driver_sql(
                "INSERT INTO meeting (id, title) VALUES (2, 'defaulted')"
            )
            connection.exec_driver_sql(
                "INSERT INTO meeting (id, title, storage_provider) "
                "VALUES (3, 'aws', 'aws_s3')"
            )
            with connection.begin_nested():
                with pytest.raises(IntegrityError):
                    connection.exec_driver_sql(
                        "INSERT INTO meeting (id, title, storage_provider) "
                        "VALUES (4, 'invalid', 'other')"
                    )

            assert connection.execute(
                text("SELECT id, storage_provider FROM meeting ORDER BY id")
            ).all() == [(1, "minio"), (2, "minio"), (3, "aws_s3")]
    finally:
        db_engine.dispose()
