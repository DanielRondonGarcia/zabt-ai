# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Mapping contract for the singleton storage configuration model."""


def test_storage_configuration_uses_id_as_its_only_primary_key():
    from app.models.storage_configuration import StorageConfiguration

    assert tuple(column.name for column in StorageConfiguration.__table__.primary_key) == (
        "id",
    )
    assert "ck_storageconfiguration_singleton" in {
        constraint.name for constraint in StorageConfiguration.__table__.constraints
    }
