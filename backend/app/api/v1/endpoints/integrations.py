# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Integration endpoints — connect/disconnect OAuth providers, calendar events."""

from typing import Any, List
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
from sqlmodel import Session

from app.api.deps import get_current_active_user, get_db
from app.core.config import settings
from app.models import User
from app.models.calendar_event import CalendarEventRead, CalendarEventUpdate
from app.models.integration import (
    IntegrationConnectResponse,
    IntegrationProvider,
    IntegrationRead,
)
from app.services.calendar_sync import calendar_sync_service
from app.services.integration import integration_service, is_token_storage_configured
from app.services.microsoft_graph import MicrosoftGraphClient, MicrosoftGraphError
from app.services.oauth_state import (
    OAuthStateError,
    OAuthStateService,
    build_pkce_challenge,
    is_safe_oauth_callback_value,
)

router = APIRouter()
oauth_state_service = OAuthStateService()


def _graph_configuration_ready() -> bool:
    return all(
        value.strip()
        for value in (
            settings.MICROSOFT_CLIENT_ID,
            settings.MICROSOFT_CLIENT_SECRET,
            settings.MICROSOFT_REDIRECT_URI,
        )
    ) and is_token_storage_configured()


def _graph_redirect(
    *,
    error_code: str | None = None,
    connected: bool = False,
) -> RedirectResponse:
    redirect_url = f"{settings.APP_URL.rstrip('/')}/integrations"
    if connected:
        redirect_url = f"{redirect_url}?{urlencode({'connected': 'microsoft'})}"
    elif error_code is not None:
        redirect_url = f"{redirect_url}?{urlencode({'microsoft_error': error_code})}"
    response = RedirectResponse(url=redirect_url, status_code=status.HTTP_302_FOUND)
    response.headers["Cache-Control"] = "no-store"
    return response


def _validated_graph_token_data(token_data: Any) -> tuple[str, str, int, list[str]]:
    if not isinstance(token_data, dict):
        raise ValueError("Invalid Microsoft Graph token response")
    access_token = token_data.get("access_token")
    refresh_token = token_data.get("refresh_token")
    if not isinstance(access_token, str) or not access_token.strip():
        raise ValueError("Microsoft Graph access token is missing")
    if not isinstance(refresh_token, str) or not refresh_token.strip():
        raise ValueError("Microsoft Graph refresh token is missing")

    expires_in = token_data.get("expires_in", 3600)
    if isinstance(expires_in, bool) or not isinstance(expires_in, (int, float)) or expires_in <= 0:
        raise ValueError("Microsoft Graph token expiry is invalid")
    scopes = token_data.get("scope", "")
    if not isinstance(scopes, str):
        raise ValueError("Microsoft Graph token scopes are invalid")
    return access_token, refresh_token, int(expires_in), scopes.split()


def _validated_graph_profile(profile: Any) -> tuple[str, str | None]:
    if not isinstance(profile, dict):
        raise ValueError("Invalid Microsoft Graph profile")
    provider_user_id = profile.get("id")
    provider_email = profile.get("email")
    if not isinstance(provider_user_id, str) or not provider_user_id.strip():
        raise ValueError("Microsoft Graph profile id is missing")
    if provider_email is not None and not isinstance(provider_email, str):
        raise ValueError("Microsoft Graph profile email is invalid")
    return provider_user_id, provider_email


def _get_graph_client() -> MicrosoftGraphClient:
    return MicrosoftGraphClient(
        client_id=settings.MICROSOFT_CLIENT_ID,
        client_secret=settings.MICROSOFT_CLIENT_SECRET,
        tenant_id=settings.MICROSOFT_TENANT_ID,
        redirect_uri=settings.MICROSOFT_REDIRECT_URI,
    )


# ── List integrations ───────────────────────────────────────────────────────

@router.get("/", response_model=List[IntegrationRead])
def list_integrations(user: User = Depends(get_current_active_user)):
    """Return all integrations for the current user."""
    return integration_service.get_for_user(user.id)


# ── Connect provider ────────────────────────────────────────────────────────

@router.post("/{provider}/connect", response_model=IntegrationConnectResponse)
def connect_provider(
    provider: str,
    user: User = Depends(get_current_active_user),
):
    """Build an OAuth authorization URL for the given provider."""
    if provider != IntegrationProvider.MICROSOFT.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported provider: {provider}",
        )

    if not _graph_configuration_ready():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Microsoft Graph connection is not configured",
        )

    try:
        transaction = oauth_state_service.create_transaction(
            purpose="graph_connect",
            user_id=user.id,
            next_path="/integrations",
        )
        client = _get_graph_client()
        auth_url = client.build_auth_url(
            transaction.state,
            code_challenge=build_pkce_challenge(transaction.code_verifier),
        )
    except (OAuthStateError, ValueError) as exc:
        if "transaction" in locals():
            try:
                oauth_state_service.consume(transaction.state)
            except OAuthStateError:
                pass
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Microsoft Graph connection is temporarily unavailable",
        ) from exc
    return IntegrationConnectResponse(auth_url=auth_url)


# ── OAuth callback ──────────────────────────────────────────────────────────

@router.get("/{provider}/callback")
async def oauth_callback(
    provider: str,
    db: Session = Depends(get_db),
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
):
    """Exchange authorization code for tokens and redirect to the app."""
    if provider != IntegrationProvider.MICROSOFT.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported provider: {provider}",
        )

    if not state or not is_safe_oauth_callback_value(state, max_length=256):
        return _graph_redirect(error_code="state")

    try:
        transaction = oauth_state_service.consume(state)
    except OAuthStateError:
        return _graph_redirect(error_code="state")
    if transaction is None or transaction.purpose != "graph_connect" or transaction.user_id is None:
        return _graph_redirect(error_code="state")

    if not is_safe_oauth_callback_value(error, max_length=128):
        return _graph_redirect(error_code="oauth_failed")
    if error is not None:
        return _graph_redirect(
            error_code="cancelled" if error == "access_denied" else "oauth_failed"
        )
    if not is_safe_oauth_callback_value(code, max_length=4096):
        return _graph_redirect(error_code="oauth_failed")
    if not code or not _graph_configuration_ready():
        return _graph_redirect(error_code="configuration")

    user = db.get(User, transaction.user_id)
    if user is None or not user.is_active:
        return _graph_redirect(error_code="account")

    try:
        client = _get_graph_client()
        token_data = await client.exchange_code(
            code,
            code_verifier=transaction.code_verifier,
        )
        access_token, refresh_token, expires_in, scopes = _validated_graph_token_data(token_data)
        profile = await client.get_user_profile(access_token)
        provider_user_id, provider_email = _validated_graph_profile(profile)

        integration_service.upsert_from_oauth(
            user_id=user.id,
            provider=IntegrationProvider.MICROSOFT,
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=expires_in,
            scopes=scopes,
            provider_user_id=provider_user_id,
            provider_email=provider_email,
        )
    except (MicrosoftGraphError, ValueError, TypeError, KeyError):
        return _graph_redirect(error_code="oauth_failed")
    except Exception:
        return _graph_redirect(error_code="oauth_failed")

    return _graph_redirect(connected=True)


# ── Disconnect provider ─────────────────────────────────────────────────────

@router.delete("/{provider}", status_code=status.HTTP_204_NO_CONTENT)
def disconnect_provider(
    provider: str,
    user: User = Depends(get_current_active_user),
):
    """Remove an integration for the given provider."""
    if provider != IntegrationProvider.MICROSOFT.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported provider: {provider}",
        )

    deleted = integration_service.disconnect(user.id, IntegrationProvider.MICROSOFT)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Integration not found",
        )


# ── Calendar events ─────────────────────────────────────────────────────────

@router.get("/calendar/events", response_model=List[CalendarEventRead])
def list_calendar_events(user: User = Depends(get_current_active_user)):
    """Return upcoming calendar events for the current user."""
    return calendar_sync_service.get_events_for_user(user.id)


@router.patch("/calendar/events/{event_id}", response_model=CalendarEventRead)
def update_calendar_event(
    event_id: int,
    body: CalendarEventUpdate,
    user: User = Depends(get_current_active_user),
):
    """Toggle auto_join for a calendar event."""
    if body.auto_join is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No update fields provided",
        )

    event = calendar_sync_service.update_auto_join(event_id, user.id, body.auto_join)
    if not event:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Calendar event not found",
        )
    return event


# ── Bot callback ───────────────────────────────────────────────────────────

@router.post("/bot-callback")
async def bot_callback(request: Request) -> Any:
    """Handle callback from bot worker when a meeting recording ends."""
    from app.services.bot_orchestration import bot_orchestration_service

    body = await request.json()
    bot_orchestration_service.handle_callback(body)
    return {"status": "ok"}
