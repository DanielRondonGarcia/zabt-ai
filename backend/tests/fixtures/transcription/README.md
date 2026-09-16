# Transcription conformance fixture

This directory intentionally contains no audio or credentials. The live test uses external WAV
and MP3 files supplied by the operator and skips unless all of the following are set:

```text
TRANSCRIPTION_CONFORMANCE=1
TRANSCRIPTION_API_KEY=<runtime-only credential>
TRANSCRIPTION_CONFORMANCE_WAV=/path/to/fixture.wav
TRANSCRIPTION_CONFORMANCE_MP3=/path/to/fixture.mp3
TRANSCRIPTION_CONFORMANCE_ORACLE_TEXT=<normalized transcript oracle>
```

Run `cd backend && uv run pytest tests/services/transcription/test_conformance.py -q` to record
the candidate model, response format, normalized output, timing and speaker gaps, duration,
metadata, and original provider usage units. The comparison model defaults to
`gpt-4o-mini-transcribe` and can be changed with `TRANSCRIPTION_CONFORMANCE_COMPARISON_MODEL`.
