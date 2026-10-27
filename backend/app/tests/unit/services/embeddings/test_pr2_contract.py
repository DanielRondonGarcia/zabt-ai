# SPDX-License-Identifier: AGPL-3.0-only
"""PR2 embedding/indexing contract tests."""

from types import SimpleNamespace
from uuid import uuid5, NAMESPACE_OID

import httpx
import pytest

from app.services.embeddings.canonicalize import canonicalize_text, flatten_structured_output
from app.services.embeddings.chunk import (
    CONTENT_CHUNK_OVERLAP_WORDS,
    CONTENT_CHUNK_WORDS,
    EMBEDDING_INPUT_MAX_BYTES,
    GROUP_CONTEXT_HEADER,
    GROUP_CONTEXT_MAX_BYTES,
    build_meeting_chunks,
    point_id_for,
)
from app.services.embeddings.ollama import OllamaEmbeddingProvider
from app.services.embeddings.openai import OpenAIEmbeddingProvider
from app.services.vector_store import EmbeddingPoint, QdrantVectorStoreClient


def _embedding_input(chunk):
    """Return the exact string each embedding adapter sends to the provider."""
    return chunk.embedding_text or chunk.text


def _utf8_len(text: str) -> int:
    return len(text.encode("utf-8"))


def test_canonicalize_and_flatten_are_deterministic():
    assert canonicalize_text("  alpha\n\t beta   gamma  ") == "alpha beta gamma"
    payload = {"b": [2, {"d": " four "}], "a": {"c": 3}}
    assert flatten_structured_output(payload) == "a.c: 3 b.0: 2 b.1.d: four"


def test_chunking_uses_overlap_and_stable_ids():
    text = " ".join(f"w{i}" for i in range(900))
    chunks = build_meeting_chunks(
        meeting_id=123,
        transcript_text=text,
        summary_text="short summary",
    )

    transcript_chunks = [chunk for chunk in chunks if chunk.kind == "transcript"]
    assert [chunk.chunk_index for chunk in transcript_chunks] == list(range(len(transcript_chunks)))
    assert all(len(chunk.text.split()) <= CONTENT_CHUNK_WORDS for chunk in transcript_chunks)
    assert transcript_chunks[0].text.split()[-CONTENT_CHUNK_OVERLAP_WORDS:] == transcript_chunks[1].text.split()[
        :CONTENT_CHUNK_OVERLAP_WORDS
    ]
    assert chunks[0].id == str(uuid5(NAMESPACE_OID, "123:summary:0"))
    assert point_id_for(123, "transcript", transcript_chunks[-1].chunk_index) == str(
        uuid5(NAMESPACE_OID, f"123:transcript:{transcript_chunks[-1].chunk_index}")
    )


def test_group_introduction_is_embedding_context_without_changing_citation_text():
    chunks = build_meeting_chunks(
        meeting_id=123,
        summary_text="short summary",
        group_introduction="This group covers product planning and customer feedback.",
    )

    assert chunks[0].text == "short summary"
    assert "Group introduction: This group covers product planning and customer feedback." in chunks[0].embedding_text
    assert chunks[0].id == point_id_for(123, "summary", 0)


def test_long_summary_and_structured_output_use_conservative_windows():
    summary = " ".join(f"summary{i}" for i in range(CONTENT_CHUNK_WORDS + 1))
    structured = {"notes": " ".join(f"structured{i}" for i in range(CONTENT_CHUNK_WORDS + 1))}

    chunks = build_meeting_chunks(
        meeting_id=123,
        summary_text=summary,
        structured_output=structured,
        structured_output_status="completed",
    )

    sources = {"summary": summary, "structured": flatten_structured_output(structured)}
    for kind, source in sources.items():
        kind_chunks = [chunk for chunk in chunks if chunk.kind == kind]
        assert all(
            _utf8_len(_embedding_input(chunk)) <= EMBEDDING_INPUT_MAX_BYTES
            for chunk in kind_chunks
        )
        assert all(len(chunk.text.split()) <= CONTENT_CHUNK_WORDS for chunk in kind_chunks)
        assert all(chunk.chunk_count == len(kind_chunks) for chunk in kind_chunks)
        assert [chunk.chunk_index for chunk in kind_chunks] == list(range(len(kind_chunks)))
        embedded_tokens = {token for chunk in kind_chunks for token in chunk.text.split()}
        assert embedded_tokens == set(source.split())
        for previous, current in zip(kind_chunks, kind_chunks[1:]):
            assert previous.text.split()[-CONTENT_CHUNK_OVERLAP_WORDS:] == current.text.split()[
                :CONTENT_CHUNK_OVERLAP_WORDS
            ]


def test_short_summary_and_structured_output_keep_single_chunk_contract():
    chunks = build_meeting_chunks(
        meeting_id=123,
        summary_text="short summary",
        structured_output={"status": "ready"},
        structured_output_status="completed",
    )

    assert [(chunk.kind, chunk.chunk_index, chunk.chunk_count, chunk.text) for chunk in chunks] == [
        ("summary", 0, 1, "short summary"),
        ("structured", 0, 1, "status: ready"),
    ]


def test_overlong_transcript_splits_every_embedding_input_to_byte_cap_without_losing_words():
    # Timestamp-like tokens make word windows approach ~4 KiB, like meeting 15's 500-ing chunk.
    text = " ".join(f"etiqueta-{index:03d}-" + "x" * 20 for index in range(300))

    chunks = build_meeting_chunks(
        meeting_id=123,
        transcript_text=text,
        summary_text="short summary",
    )

    transcript_chunks = [chunk for chunk in chunks if chunk.kind == "transcript"]
    assert transcript_chunks
    assert all(
        _utf8_len(_embedding_input(chunk)) <= EMBEDDING_INPUT_MAX_BYTES
        for chunk in chunks
    )
    # Every source token reaches some embedding slice; none is dropped or clipped.
    embedded_tokens = {token for chunk in transcript_chunks for token in chunk.text.split()}
    assert embedded_tokens == set(text.split())
    assert all(len(chunk.text.split()) <= CONTENT_CHUNK_WORDS for chunk in transcript_chunks)
    assert [chunk.chunk_index for chunk in transcript_chunks] == list(range(len(transcript_chunks)))
    assert {chunk.chunk_count for chunk in transcript_chunks} == {len(transcript_chunks)}
    assert transcript_chunks[0].id == point_id_for(123, "transcript", 0)
    assert chunks[0].kind == "summary"
    assert _embedding_input(chunks[0]) == "short summary"


def test_group_introduction_boundary_keeps_short_context_verbatim_and_bounds_long_context():
    short_summary = "resumen corto"
    boundary_context = "Contexto " + "x" * 87
    assert _utf8_len(boundary_context) == GROUP_CONTEXT_MAX_BYTES
    longer_context = "Contexto " + "x" * 88
    assert _utf8_len(longer_context) == GROUP_CONTEXT_MAX_BYTES + 1

    boundary_chunks = build_meeting_chunks(
        meeting_id=99,
        summary_text=short_summary,
        group_introduction=boundary_context,
    )
    assert boundary_chunks[0].embedding_text == (
        f"Group introduction: {boundary_context}\n\n{short_summary}"
    )

    bounded_chunks = build_meeting_chunks(
        meeting_id=99,
        summary_text=short_summary,
        group_introduction=longer_context,
    )
    bounded_input = _embedding_input(bounded_chunks[0])
    assert bounded_input.startswith("Group introduction: ")
    assert bounded_input.endswith(f"\n\n{short_summary}")
    assert _utf8_len(bounded_input) <= EMBEDDING_INPUT_MAX_BYTES
    assert "Contexto…" in bounded_input
    assert "x" * 88 not in bounded_input

    overlong_summary = " ".join(f"etiqueta-{index:03d}-" + "x" * 20 for index in range(200))
    grouped_chunks = [
        chunk
        for chunk in build_meeting_chunks(
            meeting_id=99,
            summary_text=overlong_summary,
            group_introduction=longer_context,
        )
        if chunk.kind == "summary"
    ]
    assert all(
        _utf8_len(_embedding_input(chunk)) <= EMBEDDING_INPUT_MAX_BYTES
        for chunk in grouped_chunks
    )
    assert all(
        _embedding_input(chunk).startswith("Group introduction: ")
        for chunk in grouped_chunks
    )
    assert all("Group introduction: " not in chunk.text for chunk in grouped_chunks)
    embedded_tokens = {token for chunk in grouped_chunks for token in chunk.text.split()}
    assert embedded_tokens == set(overlong_summary.split())


def test_short_windows_and_word_overlap_keep_pre_cap_chunking_contract():
    # One-byte tokens keep every 128-word window far below the byte cap, so the
    # chunker must emit the exact pre-cap windows and their 32-word overlaps.
    words = ["a", "b", "c", "d", "e"] * 400
    text = " ".join(words)
    chunks = build_meeting_chunks(meeting_id=7, transcript_text=text)
    transcript_chunks = [chunk for chunk in chunks if chunk.kind == "transcript"]

    expected_starts = range(0, len(words), CONTENT_CHUNK_WORDS - CONTENT_CHUNK_OVERLAP_WORDS)
    expected = [
        " ".join(words[start : start + CONTENT_CHUNK_WORDS])
        for start in expected_starts
    ]
    assert [chunk.text for chunk in transcript_chunks] == expected
    assert [chunk.chunk_index for chunk in transcript_chunks] == list(range(len(expected)))
    assert {chunk.chunk_count for chunk in transcript_chunks} == {len(expected)}
    assert all(_embedding_input(chunk) == chunk.text for chunk in chunks)
    assert all(
        _utf8_len(chunk.text) <= EMBEDDING_INPUT_MAX_BYTES
        for chunk in transcript_chunks
    )

    short_chunks = build_meeting_chunks(meeting_id=7, summary_text="short summary")
    assert [
        (chunk.kind, chunk.chunk_index, chunk.chunk_count, chunk.text)
        for chunk in short_chunks
    ] == [("summary", 0, 1, "short summary")]
    assert short_chunks[0].embedding_text is None


def test_oversized_utf8_word_and_grouped_context_reconstruct_bounded_inputs_in_order():
    """A no-whitespace UTF-8 word larger than the cap keeps all codepoints in
    order across emitted chunks, and an oversized single-token group
    introduction keeps visible bounded context instead of a bare ellipsis."""
    giant = "á" * 600  # 1,200 UTF-8 bytes, no whitespace
    text = f"{giant} después"
    long_context = "ñ" * 200  # single 400-byte token introduction

    transcript_chunks = [
        chunk
        for chunk in build_meeting_chunks(
            meeting_id=55,
            transcript_text=text,
            group_introduction=long_context,
        )
        if chunk.kind == "transcript"
    ]

    inputs = [_embedding_input(chunk) for chunk in transcript_chunks]
    assert inputs
    assert all(_utf8_len(value) <= EMBEDDING_INPUT_MAX_BYTES for value in inputs)
    for value in inputs:
        assert value.startswith(GROUP_CONTEXT_HEADER)
        body = value[len(GROUP_CONTEXT_HEADER) :]
        context_part, separator, window_part = body.partition("\n\n")
        assert separator
        assert window_part
        # Visible, non-empty bounded context (never a bare ellipsis): a
        # UTF-8-safe prefix of the introduction plus the ellipsis marker.
        assert context_part.endswith("…")
        preserved, marker = context_part[:-1], context_part[-1]
        assert marker == "…"
        assert 0 < _utf8_len(preserved) + _utf8_len("…") <= GROUP_CONTEXT_MAX_BYTES
        assert long_context.startswith(preserved)

    reconstructed = "".join("".join(chunk.text.split()) for chunk in transcript_chunks)
    assert reconstructed == "".join(text.split())


def test_visual_descriptions_are_chunked_with_their_time_ranges():
    chunks = build_meeting_chunks(
        meeting_id=123,
        visual_segments=[
            SimpleNamespace(
                start_time=12.5,
                end_time=18.0,
                caption="A person falls beside the doorway.",
            )
        ],
    )

    assert len(chunks) == 1
    assert chunks[0].kind == "visual"
    assert "12.50s-18.00s" in chunks[0].text
    assert "A person falls beside the doorway." in chunks[0].text
    assert chunks[0].id == point_id_for(123, "visual", 0)


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=httpx.Request("POST", "http://x"), response=httpx.Response(self.status_code, text=self.text))


class _FakeHttpClient:
    last_request = None
    requests = []

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def post(self, url, json, headers, timeout):
        self.__class__.last_request = (url, json, headers, timeout)
        self.__class__.requests.append((url, json, headers, timeout))
        return _FakeResponse({"data": [{"embedding": [0.1, 0.2, 0.3]} for _ in json["input"]]})


class _OrderedHttpClient(_FakeHttpClient):
    def post(self, url, json, headers, timeout):
        self.__class__.last_request = (url, json, headers, timeout)
        self.__class__.requests.append((url, json, headers, timeout))
        return _FakeResponse(
            {
                "data": [
                    {"embedding": [float(ord(text)), 0.2, 0.3]}
                    for text in json["input"]
                ]
            }
        )


def test_ollama_provider_batches_inputs_and_preserves_response_order(monkeypatch):
    monkeypatch.setattr("app.services.embeddings.ollama.httpx.Client", _OrderedHttpClient)
    monkeypatch.setattr("app.services.embeddings.ollama.settings.EMBEDDING_BASE_URL", "http://ollama.test/v1")
    monkeypatch.setattr("app.services.embeddings.ollama.settings.EMBEDDING_MODEL", "nomic")
    monkeypatch.setattr("app.services.embeddings.ollama.settings.EMBEDDING_DIMENSION", 3)
    monkeypatch.setattr("app.services.embeddings.ollama.settings.EMBEDDING_MAX_BATCH", 2)
    _FakeHttpClient.requests = []
    provider = OllamaEmbeddingProvider()

    assert provider.dimension == 3
    assert provider.embed(["a", "b", "c", "d", "e"]) == [
        [97.0, 0.2, 0.3],
        [98.0, 0.2, 0.3],
        [99.0, 0.2, 0.3],
        [100.0, 0.2, 0.3],
        [101.0, 0.2, 0.3],
    ]
    assert [_request[1]["input"] for _request in _OrderedHttpClient.requests] == [
        ["a", "b"],
        ["c", "d"],
        ["e"],
    ]
    assert _OrderedHttpClient.last_request[0] == "http://ollama.test/v1/embeddings"


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"data": []}, "count mismatch"),
        ({"data": [{"embedding": [0.1, 0.2]}]}, "dimension mismatch"),
    ],
)
def test_ollama_provider_validates_each_batch_count_and_dimension(monkeypatch, payload, message):
    class InvalidResponseHttpClient(_FakeHttpClient):
        calls = 0

        def post(self, url, json, headers, timeout):
            type(self).calls += 1
            if type(self).calls == 2:
                return _FakeResponse(payload)
            return _FakeResponse({"data": [{"embedding": [0.1, 0.2, 0.3]} for _ in json["input"]]})

    monkeypatch.setattr("app.services.embeddings.ollama.httpx.Client", InvalidResponseHttpClient)
    monkeypatch.setattr("app.services.embeddings.ollama.settings.EMBEDDING_BASE_URL", "http://ollama.test/v1")
    monkeypatch.setattr("app.services.embeddings.ollama.settings.EMBEDDING_MODEL", "nomic")
    monkeypatch.setattr("app.services.embeddings.ollama.settings.EMBEDDING_DIMENSION", 3)
    monkeypatch.setattr("app.services.embeddings.ollama.settings.EMBEDDING_MAX_BATCH", 1)

    with pytest.raises(ValueError, match=message):
        OllamaEmbeddingProvider().embed(["a", "b"])


def test_openai_provider_requires_key_and_dimension(monkeypatch):
    monkeypatch.setattr("app.services.embeddings.openai.httpx.Client", _FakeHttpClient)
    monkeypatch.setattr("app.services.embeddings.openai.settings.EMBEDDING_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setattr("app.services.embeddings.openai.settings.EMBEDDING_MODEL", "text-embedding-3-small")
    monkeypatch.setattr("app.services.embeddings.openai.settings.EMBEDDING_API_KEY", "")
    monkeypatch.setattr("app.services.embeddings.openai.settings.OPENAI_API_KEY", "")
    with pytest.raises(ValueError, match="required"):
        OpenAIEmbeddingProvider()

    monkeypatch.setattr("app.services.embeddings.openai.settings.EMBEDDING_API_KEY", "sk-test")
    monkeypatch.setattr("app.services.embeddings.openai.settings.EMBEDDING_DIMENSION", 4)
    provider = OpenAIEmbeddingProvider()
    with pytest.raises(ValueError, match="dimension mismatch"):
        provider.embed(["hello"])


def test_openai_provider_uses_actsis_key_for_custom_endpoint(monkeypatch):
    monkeypatch.setattr("app.services.embeddings.openai.httpx.Client", _FakeHttpClient)
    monkeypatch.setattr("app.services.embeddings.openai.settings.EMBEDDING_BASE_URL", "https://ai.actsis.internal/v1")
    monkeypatch.setattr("app.services.embeddings.openai.settings.EMBEDDING_MODEL", "nomic-embed")
    monkeypatch.setattr("app.services.embeddings.openai.settings.EMBEDDING_API_KEY", "")
    monkeypatch.setattr("app.services.embeddings.openai.settings.ACTSIS_API_KEY", "actsis-test-key")
    monkeypatch.setattr("app.services.embeddings.openai.settings.OPENAI_API_KEY", "official-key-must-not-leak")
    monkeypatch.setattr("app.services.embeddings.openai.settings.EMBEDDING_DIMENSION", 3)

    provider = OpenAIEmbeddingProvider()
    assert provider.embed(["hello"]) == [[0.1, 0.2, 0.3]]
    assert _FakeHttpClient.last_request[0] == "https://ai.actsis.internal/v1/embeddings"
    assert _FakeHttpClient.last_request[2]["Authorization"] == "Bearer actsis-test-key"


def test_openai_provider_batches_inputs_and_preserves_response_order(monkeypatch):
    monkeypatch.setattr("app.services.embeddings.openai.httpx.Client", _OrderedHttpClient)
    monkeypatch.setattr("app.services.embeddings.openai.settings.EMBEDDING_BASE_URL", "https://ai.actsis.internal/v1")
    monkeypatch.setattr("app.services.embeddings.openai.settings.EMBEDDING_MODEL", "nomic-embed")
    monkeypatch.setattr("app.services.embeddings.openai.settings.EMBEDDING_API_KEY", "actsis-test-key")
    monkeypatch.setattr("app.services.embeddings.openai.settings.EMBEDDING_DIMENSION", 3)
    monkeypatch.setattr("app.services.embeddings.openai.settings.EMBEDDING_MAX_BATCH", 2)
    _FakeHttpClient.requests = []

    provider = OpenAIEmbeddingProvider()

    assert provider.embed(["a", "b", "c"]) == [
        [97.0, 0.2, 0.3],
        [98.0, 0.2, 0.3],
        [99.0, 0.2, 0.3],
    ]
    assert [_request[1]["input"] for _request in _OrderedHttpClient.requests] == [["a", "b"], ["c"]]


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"data": []}, "count mismatch"),
        ({"data": [{"embedding": [0.1, 0.2]}]}, "dimension mismatch"),
    ],
)
def test_openai_provider_validates_each_batch_count_and_dimension(monkeypatch, payload, message):
    class InvalidResponseHttpClient(_FakeHttpClient):
        calls = 0

        def post(self, url, json, headers, timeout):
            type(self).calls += 1
            if type(self).calls == 2:
                return _FakeResponse(payload)
            return _FakeResponse({"data": [{"embedding": [0.1, 0.2, 0.3]} for _ in json["input"]]})

    monkeypatch.setattr("app.services.embeddings.openai.httpx.Client", InvalidResponseHttpClient)
    monkeypatch.setattr("app.services.embeddings.openai.settings.EMBEDDING_BASE_URL", "https://ai.actsis.internal/v1")
    monkeypatch.setattr("app.services.embeddings.openai.settings.EMBEDDING_MODEL", "nomic-embed")
    monkeypatch.setattr("app.services.embeddings.openai.settings.EMBEDDING_API_KEY", "actsis-test-key")
    monkeypatch.setattr("app.services.embeddings.openai.settings.EMBEDDING_DIMENSION", 3)
    monkeypatch.setattr("app.services.embeddings.openai.settings.EMBEDDING_MAX_BATCH", 1)

    with pytest.raises(ValueError, match=message):
        OpenAIEmbeddingProvider().embed(["a", "b"])


class _FakeQdrant:
    def __init__(self):
        self.collections = set()
        self.upserts = []
        self.deletes = []
        self.searches = []
        self.indexes = []
        self.upsert_wait_options = []
        self.delete_wait_options = []

    def collection_exists(self, collection_name):
        return collection_name in self.collections

    def create_collection(self, collection_name, vectors_config):
        self.collections.add(collection_name)
        self.vectors_config = vectors_config

    def create_payload_index(self, collection_name, field_name, field_schema):
        self.indexes.append((collection_name, field_name, field_schema))

    def upsert(self, collection_name, points, **kwargs):
        self.upserts.append((collection_name, points))
        self.upsert_wait_options.append(kwargs)

    def delete(self, collection_name, points_selector, **kwargs):
        self.deletes.append((collection_name, points_selector))
        self.delete_wait_options.append(kwargs)

    def search(self, collection_name, query_vector, query_filter, limit):
        self.searches.append((collection_name, query_vector, query_filter, limit))
        return [SimpleNamespace(id="p1", score=0.9, payload={"meeting_id": 7}, vector=None)]


def test_vector_store_filters_owner_group_and_uses_deterministic_ids(monkeypatch):
    fake = _FakeQdrant()
    monkeypatch.setattr("app.services.vector_store.settings.EMBEDDING_PROVIDER", "ollama")
    monkeypatch.setattr("app.services.vector_store.settings.EMBEDDING_DIMENSION", 3)
    monkeypatch.setattr("app.services.vector_store.settings.QDRANT_COLLECTION_PREFIX", "meeting_embeddings")
    monkeypatch.setattr("app.services.vector_store.settings.EMBEDDING_MODEL", "nomic")
    client = QdrantVectorStoreClient(client=fake)

    client.upsert_points([
        EmbeddingPoint(
            id=point_id_for(7, "summary", 0),
            vector=[0.1, 0.2, 0.3],
            text="summary",
            owner_id=5,
            group_id=9,
            meeting_id=7,
            kind="summary",
            chunk_index=0,
            chunk_count=1,
            source_type="upload",
            model="nomic",
        )
    ])
    client.delete_by_filter(owner_id=5, group_id=9, meeting_id=7)
    result = client.search_filtered([0.1, 0.2, 0.3], owner_id=5, group_id=9, kinds=["summary"], limit=3)

    assert fake.upserts[0][0] == "meeting_embeddings_ollama_3"
    assert fake.upserts[0][1][0].id == point_id_for(7, "summary", 0)
    assert fake.upsert_wait_options == [{"wait": True}]
    assert fake.delete_wait_options == [{"wait": True}]
    assert result[0]["payload"] == {"meeting_id": 7}
    search_filter = fake.searches[0][2]
    rendered = repr(search_filter)
    assert "owner_id" in rendered and "group_id" in rendered and "summary" in rendered


def test_vector_search_rejects_missing_group_scope(monkeypatch):
    fake = _FakeQdrant()
    monkeypatch.setattr("app.services.vector_store.settings.EMBEDDING_PROVIDER", "ollama")
    monkeypatch.setattr("app.services.vector_store.settings.EMBEDDING_DIMENSION", 3)
    client = QdrantVectorStoreClient(client=fake)

    with pytest.raises(ValueError, match="group_id is required"):
        client.search_filtered([0.1, 0.2, 0.3], owner_id=None, group_id=None)

    assert fake.searches == []


def test_ungrouped_points_store_without_group_and_remain_group_filtered(monkeypatch):
    fake = _FakeQdrant()
    monkeypatch.setattr("app.services.vector_store.settings.EMBEDDING_PROVIDER", "ollama")
    monkeypatch.setattr("app.services.vector_store.settings.EMBEDDING_DIMENSION", 3)
    client = QdrantVectorStoreClient(client=fake)

    client.upsert_points([
        EmbeddingPoint(
            id=point_id_for(7, "visual", 0),
            vector=[0.1, 0.2, 0.3],
            text="12.50s-18.00s A person falls beside the doorway.",
            owner_id=5,
            group_id=None,
            meeting_id=7,
            kind="visual",
            chunk_index=0,
            chunk_count=1,
            source_type="upload",
            model="nomic",
        )
    ])

    assert "group_id" not in fake.upserts[0][1][0].payload
    client.search_filtered([0.1, 0.2, 0.3], owner_id=5, group_id=9)
    conditions = fake.searches[0][2].must
    assert [(condition.key, condition.match.value) for condition in conditions] == [
        ("owner_id", 5),
        ("group_id", 9),
    ]
