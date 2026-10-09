# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Authenticated user profile endpoints."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from app.api.deps import get_current_active_user
from app.api.v1.endpoints.auth import UserRead
from app.models import CustomAIProvider, User
from app.models.ai_provider import AIProviderConfiguration
from app.services.ai_provider_configuration import (
    AIProviderConfigurationValidationError,
    AIProviderSecretStorageError,
    MAX_BASE_URL_LENGTH,
    ai_provider_configuration_service,
    normalize_api_key,
    normalize_base_url,
    normalize_model,
)
from app.services.ai_provider_models import (
    MAX_MODEL_DISCOVERY_MODELS,
    AIProviderModelDiscoveryError,
    ai_provider_model_discovery_service,
)


router = APIRouter()


class AIProviderConfigurationRead(BaseModel):
    """Safe current-user view; the encrypted or plaintext key is never exposed."""

    provider: CustomAIProvider | None
    model: str | None
    base_url: str | None
    api_key_configured: bool
    enabled: bool
    use_for_summary: bool
    use_for_chat: bool


class AIProviderConfigurationPatch(BaseModel):
    provider: CustomAIProvider | None = None
    model: str | None = None
    base_url: str | None = None
    api_key: SecretStr | None = None
    enabled: bool | None = None
    use_for_summary: bool | None = None
    use_for_chat: bool | None = None

    @field_validator("model")
    @classmethod
    def validate_model(cls, value: str | None) -> str | None:
        return None if value is None else normalize_model(value)

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str | None) -> str | None:
        return normalize_base_url(value)

    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None:
            return None
        # SecretStr keeps Pydantic's validation representation masked. The raw
        # value is unwrapped only inside the encrypted persistence boundary.
        normalize_api_key(value.get_secret_value())
        return value


class AIProviderModelsRequest(BaseModel):
    """Transient discovery input; credentials are never persisted or returned."""

    model_config = ConfigDict(extra="forbid")

    provider: CustomAIProvider | None = None
    base_url: str | None = Field(default=None, max_length=MAX_BASE_URL_LENGTH)
    api_key: SecretStr | None = None

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str | None) -> str | None:
        return normalize_base_url(value)

    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None:
            normalize_api_key(value.get_secret_value())
        return value


class AIProviderModelsRead(BaseModel):
    """Safe model-catalog response with no provider credential fields."""

    models: list[str] = Field(max_length=MAX_MODEL_DISCOVERY_MODELS)
    source: Literal[
        "openai-compatible",
        "anthropic",
        "ollama-compatible",
        "ollama-tags",
    ]


def _ai_provider_read(
    configuration: AIProviderConfiguration | None,
) -> AIProviderConfigurationRead:
    if configuration is None:
        return AIProviderConfigurationRead(
            provider=None,
            model=None,
            base_url=None,
            api_key_configured=False,
            enabled=False,
            use_for_summary=False,
            use_for_chat=False,
        )
    return AIProviderConfigurationRead(
        provider=CustomAIProvider(configuration.provider),
        model=configuration.model,
        base_url=configuration.base_url,
        api_key_configured=bool(configuration.encrypted_api_key),
        enabled=configuration.enabled,
        use_for_summary=configuration.use_for_summary,
        use_for_chat=configuration.use_for_chat,
    )


@router.get("/me", response_model=UserRead)
def get_current_user_profile(user: User = Depends(get_current_active_user)) -> UserRead:
    return UserRead.model_validate(user, from_attributes=True)


@router.get("/me/ai-provider", response_model=AIProviderConfigurationRead)
def get_ai_provider_configuration(
    response: Response,
    user: User = Depends(get_current_active_user),
) -> AIProviderConfigurationRead:
    """Return only safe custom-provider settings for the current user."""
    response.headers["Cache-Control"] = "no-store"
    return _ai_provider_read(ai_provider_configuration_service.get_for_user(user.id))


@router.post("/me/ai-provider/models", response_model=AIProviderModelsRead)
def discover_ai_provider_models(
    payload: AIProviderModelsRequest,
    response: Response,
    user: User = Depends(get_current_active_user),
) -> AIProviderModelsRead:
    """Discover models for the current user's transient provider settings."""
    response.headers["Cache-Control"] = "no-store"
    api_key = (
        payload.api_key.get_secret_value()
        if payload.api_key is not None
        else None
    )
    try:
        catalog = ai_provider_model_discovery_service.discover_for_user(
            user.id,
            provider=payload.provider,
            base_url=payload.base_url,
            api_key=api_key,
            fields_set=payload.model_fields_set,
        )
    except AIProviderConfigurationValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from None
    except AIProviderModelDiscoveryError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from None
    return AIProviderModelsRead(models=catalog.models, source=catalog.source)


@router.patch("/me/ai-provider", response_model=AIProviderConfigurationRead)
def update_ai_provider_configuration(
    payload: AIProviderConfigurationPatch,
    user: User = Depends(get_current_active_user),
) -> AIProviderConfigurationRead:
    """Create or update the current user's encrypted completion settings."""
    if not payload.model_fields_set:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one AI provider setting is required.",
        )

    api_key = (
        payload.api_key.get_secret_value()
        if payload.api_key is not None
        else None
    )
    try:
        configuration = ai_provider_configuration_service.upsert(
            user.id,
            provider=payload.provider,
            model=payload.model,
            base_url=payload.base_url,
            api_key=api_key,
            enabled=payload.enabled,
            use_for_summary=payload.use_for_summary,
            use_for_chat=payload.use_for_chat,
            fields_set=payload.model_fields_set,
        )
    except AIProviderConfigurationValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from None
    except AIProviderSecretStorageError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from None
    return _ai_provider_read(configuration)


@router.delete("/me/ai-provider", status_code=status.HTTP_204_NO_CONTENT)
def delete_ai_provider_configuration(
    user: User = Depends(get_current_active_user),
) -> Response:
    """Remove the current user's custom provider and restore application defaults."""
    ai_provider_configuration_service.delete_for_user(user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
