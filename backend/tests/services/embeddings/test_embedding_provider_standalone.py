# SPDX-License-Identifier: AGPL-3.0-only
"""Standalone deterministic embedding helper tests."""

from app.services.embeddings.chunk import point_id_for


def test_point_id_is_stable():
    assert point_id_for(1, "summary", 0) == point_id_for(1, "summary", 0)
    assert point_id_for(1, "summary", 0) != point_id_for(1, "summary", 1)
