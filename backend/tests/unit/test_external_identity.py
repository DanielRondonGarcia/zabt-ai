# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Model and migration shape checks for external OIDC identities."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from app.models import ExternalIdentity, ExternalIdentityProvider


def _migration_module():
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "c8d9e0f1a2b3_add_external_identities.py"
    )
    spec = importlib.util.spec_from_file_location("external_identity_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_external_identity_model_has_provider_subject_uniqueness_and_cascade():
    table = ExternalIdentity.__table__
    unique = next(
        constraint
        for constraint in table.constraints
        if constraint.name == "uq_externalidentity_provider_tenant_subject"
    )

    assert [column.name for column in unique.columns] == ["provider", "tenant_id", "subject"]
    assert ExternalIdentityProvider.MICROSOFT.value == "microsoft"
    foreign_key = next(iter(table.c.user_id.foreign_keys))
    assert foreign_key.ondelete == "CASCADE"
    assert {"access_token", "refresh_token", "id_token"}.isdisjoint(table.c.keys())
    assert {"ix_externalidentity_user_id", "ix_externalidentity_user_provider"}.issubset(
        {index.name for index in table.indexes}
    )


def test_external_identity_migration_revises_current_head_and_is_reversible():
    migration = _migration_module()

    assert migration.revision == "c8d9e0f1a2b3"
    assert migration.down_revision == "b7c8d9e0f1a2"
    source = Path(migration.__file__).read_text(encoding="utf-8")
    assert 'ondelete="CASCADE"' in source
    assert 'op.drop_table("externalidentity")' in source
