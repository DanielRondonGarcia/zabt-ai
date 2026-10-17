from app.services import meeting_intelligence


def test_meeting_intelligence_client_uses_ca_aware_langfuse_factory(monkeypatch):
    created_client = object()
    builder_calls = []

    def fake_build_openai_client(**kwargs):
        builder_calls.append(kwargs)
        return created_client

    monkeypatch.setattr(
        meeting_intelligence,
        "build_openai_client",
        fake_build_openai_client,
        raising=False,
    )

    assert meeting_intelligence._build_openai_client() is created_client
    assert builder_calls == [{
        "base_url": meeting_intelligence.settings.OPENAI_BASE_URL,
        "api_key": meeting_intelligence.settings.OPENAI_API_KEY,
        "client_factory": meeting_intelligence.OpenAI,
    }]
