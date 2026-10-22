"""Model output and its resume marker must commit together (offline services)."""
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("operation,stage", [
    ("summary", "stage_summarize"),
    ("title", "stage_infer_title"),
    ("structured", "stage_extract_structured_output"),
    ("highlights", "stage_extract_highlights"),
])
def test_output_completion_is_in_the_output_transaction(monkeypatch, operation, stage):
    from app.services.meeting import meeting_service
    from app.services.meeting_intelligence import intelligence_service
    from app.services.meeting_processing_audit import meeting_processing_audit
    import app.services.meeting as meetings
    import app.services.meeting_intelligence as intelligence

    row = SimpleNamespace(id=15, owner_id=7, title="recording.mp3", transcript_text="words")
    run = SimpleNamespace(id=101, owner_id=7)
    added, committed = [], []

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def add(self, item):
            added.append(item)

        def commit(self):
            committed.extend(added)

        def refresh(self, item):
            pass

        def exec(self, query):
            return SimpleNamespace(all=lambda: [])

    monkeypatch.setattr(meetings, "Session", lambda engine: Session())
    monkeypatch.setattr(intelligence, "Session", lambda engine: Session())
    monkeypatch.setattr(meeting_processing_audit, "_locked_current_run", lambda *a, **k: (row, run))
    identity = dict(processing_run_id=101, dispatch_attempt_id="attempt", owner_id=7)
    if operation == "summary":
        meeting_service.save_summary_for_attempt(15, 101, "attempt", "Summary", audit_task_id="task")
    elif operation == "title":
        meeting_service.save_inferred_title_for_attempt(15, 101, "attempt", "Title", owner_id=7, audit_task_id="task")
    elif operation == "structured":
        meeting_service.complete_intelligence_for_attempt(15, {"meeting_type": "generic"}, **identity, audit_task_id="task")
    else:
        intelligence_service.save_highlights(15, {}, **identity, audit_task_id="task")
    events = [item for item in committed if getattr(item, "event_type", None) == "completed"]
    assert len(events) == 1
    event = events[0]
    assert (event.stage, event.task_id, event.run_id, event.dispatch_attempt_id) == (stage, "task", 101, "attempt")
    assert event.metadata_["transcript_fingerprint"]
