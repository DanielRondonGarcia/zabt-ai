# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Provider-independent resolution of local paths and object-storage keys."""

from __future__ import annotations

from pathlib import Path

from app.services.transcription.errors import InvalidAudioSourceError
from app.services.transcription.contracts import AudioSource, AudioSourceKind, ProviderName


class AudioSourceResolver:
    """Create explicit sources without converting storage keys into local paths."""

    @staticmethod
    def local_path(value: str | Path) -> AudioSource:
        path = Path(value).expanduser() if str(value).strip() else None
        if path is None or not str(path):
            raise InvalidAudioSourceError("A local audio path is required")
        return AudioSource(local_path=path)

    @staticmethod
    def storage_key(value: str) -> AudioSource:
        key = value.strip()
        if not key:
            raise InvalidAudioSourceError("An audio storage key is required")
        return AudioSource(storage_key=key)

    @classmethod
    def resolve(
        cls,
        source: AudioSource | str | Path,
        *,
        kind: AudioSourceKind | str | None = None,
        storage_key: str | None = None,
    ) -> AudioSource:
        if isinstance(source, AudioSource):
            if storage_key is not None:
                raise InvalidAudioSourceError("Audio source and storage_key cannot both be supplied")
            return source
        if storage_key is not None:
            return cls.storage_key(storage_key)
        if kind in (AudioSourceKind.STORAGE_KEY, AudioSourceKind.STORAGE_KEY.value):
            return cls.storage_key(str(source))
        if kind in (AudioSourceKind.LOCAL_PATH, AudioSourceKind.LOCAL_PATH.value):
            return cls.local_path(source)
        if isinstance(source, Path) or Path(str(source)).is_absolute() or Path(str(source)).exists():
            return cls.local_path(source)
        return cls.storage_key(str(source))

    @classmethod
    def resolve_for_provider(
        cls,
        source: AudioSource | str | Path,
        provider: ProviderName | str,
        *,
        storage_key: str | None = None,
    ) -> AudioSource:
        """Prefer the existing storage key for GPU/RunPod signed-URL requests."""
        provider_value = provider.value if isinstance(provider, ProviderName) else provider
        if storage_key is not None and provider_value in {ProviderName.GPU_LOCAL.value, ProviderName.RUNPOD.value}:
            return cls.storage_key(storage_key)
        return cls.resolve(source)
