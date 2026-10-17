# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Mapping contract for per-meeting object-storage provider metadata."""


def test_meeting_storage_provider_defaults_existing_rows_to_minio():
    from app.models.base import Meeting

    meeting = Meeting(title="existing meeting")
    column = Meeting.__table__.columns["storage_provider"]

    assert meeting.storage_provider == "minio"
    assert not column.nullable
    assert str(column.server_default.arg).strip("'") == "minio"
