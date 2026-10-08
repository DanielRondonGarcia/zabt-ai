# SPDX-License-Identifier: AGPL-3.0-only
"""Migration contract tests for the system role and global login mode."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def load_migration():
    path = (
        Path(__file__).parents[2]
        / "alembic"
        / "versions"
        / "f1a2b3c4d5e6_add_system_superuser_and_authentication_mode.py"
    )
    spec = importlib.util.spec_from_file_location("authentication_mode_migration", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_upgrade_defaults_superuser_to_false_and_creates_the_local_singleton(
    monkeypatch,
):
    migration = load_migration()
    added_columns = []
    created_tables = []
    created_indexes = []
    executed_sql = []

    monkeypatch.setattr(migration.op, "add_column", lambda *args: added_columns.append(args))
    monkeypatch.setattr(migration.op, "alter_column", lambda *args, **kwargs: None)
    monkeypatch.setattr(migration.op, "create_index", lambda *args, **kwargs: created_indexes.append(args))
    monkeypatch.setattr(
        migration.op,
        "create_table",
        lambda *args, **kwargs: created_tables.append((args, kwargs)),
    )
    monkeypatch.setattr(migration.op, "execute", lambda statement: executed_sql.append(statement))

    migration.upgrade()

    assert migration.down_revision == "e0f1a2b3c4d5"
    assert added_columns[0][0] == "user"
    assert added_columns[0][1].name == "is_superuser"
    assert str(added_columns[0][1].server_default.arg).casefold() in {"false", "false()"}
    assert "ix_user_is_superuser" in [item[0] for item in created_indexes]

    table_args, _ = created_tables[0]
    assert table_args[0] == "authenticationconfiguration"
    assert "ck_authenticationconfiguration_singleton_id" in str(table_args)
    assert "ck_authenticationconfiguration_user_login_mode" in str(table_args)
    foreign_keys = [item for item in table_args if item.__class__.__name__ == "ForeignKeyConstraint"]
    assert len(foreign_keys) == 1
    assert foreign_keys[0].ondelete == "SET NULL"
    assert len(executed_sql) == 1
    assert "'local'" in str(executed_sql[0])
    assert 'UPDATE "user"' not in str(executed_sql[0])
    assert "is_admin" not in str(executed_sql[0])
    assert "MIN(id)" not in str(executed_sql[0])
