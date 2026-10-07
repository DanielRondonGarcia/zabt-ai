# SPDX-License-Identifier: AGPL-3.0-only
"""Migration contract tests for admin bootstrap and public OIDC storage."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def load_migration():
    path = (
        Path(__file__).parents[2]
        / "alembic"
        / "versions"
        / "e0f1a2b3c4d5_add_global_microsoft_oidc_configuration.py"
    )
    spec = importlib.util.spec_from_file_location("microsoft_oidc_migration", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_upgrade_marks_only_the_earliest_existing_user_and_creates_no_secret_column(
    monkeypatch,
):
    migration = load_migration()
    added_columns = []
    executed_sql = []
    created_tables = []

    monkeypatch.setattr(migration.op, "add_column", lambda *args: added_columns.append(args))
    monkeypatch.setattr(migration.op, "execute", lambda statement: executed_sql.append(statement))
    monkeypatch.setattr(migration.op, "alter_column", lambda *args, **kwargs: None)
    monkeypatch.setattr(migration.op, "create_index", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        migration.op,
        "create_table",
        lambda *args, **kwargs: created_tables.append((args, kwargs)),
    )

    migration.upgrade()

    assert added_columns[0][0] == "user"
    assert added_columns[0][1].name == "is_admin"
    assert "MIN(id)" in str(executed_sql[0])
    table_args, _ = created_tables[0]
    table_name = table_args[0]
    column_names = {
        item.name
        for item in table_args[1:]
        if hasattr(item, "name")
    }
    assert table_name == "microsoftoidcconfiguration"
    assert {"client_id", "tenant_id", "redirect_uri", "enabled", "updated_by"}.issubset(
        column_names
    )
    assert not any(
        isinstance(name, str) and "secret" in name.casefold()
        for name in column_names
    )
