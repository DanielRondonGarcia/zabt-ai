# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
from fastapi import APIRouter
from app.api.v1.endpoints import (
    ai_chat,
    auth,
    devices,
    groups,
    health,
    highlights,
    integrations,
    languages,
    meetings,
    styles,
    templates,
    transcriptions,
    uploads,
    users,
    webhooks,
    billing,
)

api_router = APIRouter()
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(users.router, prefix="/users", tags=["users"])
api_router.include_router(meetings.router, prefix="/meetings", tags=["meetings"])
api_router.include_router(transcriptions.router, prefix="/transcriptions", tags=["transcriptions"])
api_router.include_router(styles.router, prefix="/styles", tags=["styles"])
api_router.include_router(billing.router, prefix="/billing", tags=["billing"])
api_router.include_router(webhooks.router, prefix="/webhooks", tags=["webhooks"])
api_router.include_router(health.router, prefix="/health", tags=["health"])
api_router.include_router(templates.router, prefix="/templates", tags=["templates"])
api_router.include_router(groups.router, prefix="/groups", tags=["groups"])
api_router.include_router(ai_chat.router, prefix="/ai-chat", tags=["ai-chat"])
api_router.include_router(integrations.router, prefix="/integrations", tags=["integrations"])
api_router.include_router(highlights.router, tags=["highlights"])
api_router.include_router(devices.router, prefix="/devices", tags=["devices"])
api_router.include_router(uploads.router, tags=["uploads"])
api_router.include_router(languages.router, tags=["languages"])
