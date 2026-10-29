# SPDX-License-Identifier: AGPL-3.0-only
"""Batched Qdrant upsert contract tests (see odd/tasks/batched-qdrant-upsert.md S1-S3)."""

import pytest

from app.services.embeddings.chunk import point_id_for
from app.services.vector_store import (
    QDRANT_UPSERT_BATCH_POINTS,
    EmbeddingPoint,
    QdrantVectorStoreClient,
)


class _UpsertFailure(Exception):
    pass


class _BatchRecordingQdrant:
    """Fake Qdrant client recording one entry per upsert call."""

    def __init__(self, fail_on_call: int | None = None):
        self.collections = set()
        self.upserts = []
        self.upsert_wait_options = []
        self.fail_on_call = fail_on_call

    def collection_exists(self, collection_name):
        return collection_name in self.collections

    def create_collection(self, collection_name, vectors_config):
        self.collections.add(collection_name)

    def create_payload_index(self, collection_name, field_name, field_schema):
        self.indexes = getattr(self, "indexes", [])
        self.indexes.append((collection_name, field_name, field_schema))

    def upsert(self, collection_name, points, **kwargs):
        if self.fail_on_call is not None and len(self.upserts) == self.fail_on_call:
            raise _UpsertFailure(
                "JSON payload (47383887 bytes) is larger than allowed (limit: 33554432 bytes)"
            )
        self.upserts.append((collection_name, points))
        self.upsert_wait_options.append(kwargs)


def _point(meeting_id: int, index: int, total: int) -> EmbeddingPoint:
    return EmbeddingPoint(
        id=point_id_for(meeting_id, "transcript", index),
        vector=[float(index), 0.2, 0.3],
        text=f"chunk {index}",
        owner_id=5,
        group_id=9,
        meeting_id=meeting_id,
        kind="transcript",
        chunk_index=index,
        chunk_count=total,
        source_type="upload",
        model="nomic",
    )


def _client(fake) -> QdrantVectorStoreClient:
    return QdrantVectorStoreClient(client=fake)


def test_upsert_batch_constant_is_128_points():
    """The deterministic fixed batch count is an explicit documented constant."""
    assert QDRANT_UPSERT_BATCH_POINTS == 128


def test_upsert_points_splits_large_sets_into_bounded_batches():
    fake = _BatchRecordingQdrant()
    points = [_point(7, index, 300) for index in range(300)]

    _client(fake).upsert_points(points)

    assert [len(batch_points) for _, batch_points in fake.upserts] == [128, 128, 44]
    # One client.upsert per batch, all waiting for Qdrant to apply the batch.
    assert fake.upsert_wait_options == [{"wait": True}, {"wait": True}, {"wait": True}]
    # All batches target the same active collection.
    client = _client(fake)
    assert {collection for collection, _ in fake.upserts} == {client.active_collection}
    # Point order is preserved across batch boundaries.
    sent_ids = [point.id for _, batch_points in fake.upserts for point in batch_points]
    assert sent_ids == [point.id for point in points]
    first = fake.upserts[0][1][0]
    assert first.payload == {
        "text": "chunk 0",
        "owner_id": 5,
        "meeting_id": 7,
        "kind": "transcript",
        "chunk_index": 0,
        "chunk_count": 300,
        "source_type": "upload",
        "model": "nomic",
        "group_id": 9,
    }


def test_upsert_points_empty_list_sends_nothing():
    fake = _BatchRecordingQdrant()

    _client(fake).upsert_points([])

    assert fake.upserts == []
    assert not fake.collections


def test_upsert_points_single_point_keeps_single_wait_call():
    fake = _BatchRecordingQdrant()

    _client(fake).upsert_points([_point(7, 0, 1)])

    assert len(fake.upserts) == 1
    assert fake.upserts[0][1][0].id == point_id_for(7, "transcript", 0)
    assert fake.upsert_wait_options == [{"wait": True}]


def test_batch_failure_raises_and_remaining_batches_are_not_sent():
    fake = _BatchRecordingQdrant(fail_on_call=1)
    points = [_point(7, index, 300) for index in range(300)]

    with pytest.raises(_UpsertFailure, match="larger than allowed"):
        _client(fake).upsert_points(points)

    # Only the accepted first batch was sent; no later batches were attempted.
    assert len(fake.upserts) == 1
    assert len(fake.upserts[0][1]) == 128
    # A retried reindex re-upserts everything idempotently by point id.
    fake2 = _BatchRecordingQdrant()
    _client(fake2).upsert_points(points)
    sent_ids = [point.id for _, batch_points in fake2.upserts for point in batch_points]
    assert sent_ids == [point.id for point in points]
