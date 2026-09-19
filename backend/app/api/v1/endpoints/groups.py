# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
from typing import Any, Literal
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api import deps
from app.models import GroupCreate, GroupRead, GroupUpdate, User
from app.services.group import group_service
from app.services.retrieval import retrieval_service

router = APIRouter()


class GroupSearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=4000)
    limit: int = Field(default=10, ge=1, le=50)
    kinds: list[str] | None = None


class GroupSearchResult(BaseModel):
    meeting_id: int
    kind: str
    chunk_index: int
    score: float
    text: str


class GroupSearchResponse(BaseModel):
    group_id: int
    results: list[GroupSearchResult]


class GroupReindexResponse(BaseModel):
    status: Literal["accepted"]
    task_id: str


class _LazyReindexGroupTask:
    def delay(self, group_id: int):
        from app.worker import reindex_group as worker_reindex_group

        return worker_reindex_group.delay(group_id)


reindex_group = _LazyReindexGroupTask()


@router.get("/", response_model=list[GroupRead])
def list_groups(
    current_user: User = Depends(deps.get_current_active_user),
) -> Any:
    return group_service.list_for_user(current_user.id)


@router.post("/", response_model=GroupRead, status_code=201)
def create_group(
    *,
    payload: GroupCreate,
    current_user: User = Depends(deps.get_current_active_user),
) -> Any:
    return group_service.create(current_user.id, payload.name, payload.description)


@router.get("/{group_id}", response_model=GroupRead)
def get_group(
    group_id: int,
    current_user: User = Depends(deps.get_current_active_user),
) -> Any:
    return group_service.get_accessible(group_id, current_user.id)


@router.patch("/{group_id}", response_model=GroupRead)
def update_group(
    *,
    group_id: int,
    payload: GroupUpdate,
    current_user: User = Depends(deps.get_current_active_user),
) -> Any:
    return group_service.update(
        group_id, current_user.id, payload.name, payload.description
    )


@router.post("/{group_id}/search", response_model=GroupSearchResponse)
def search_group(
    *,
    group_id: int,
    payload: GroupSearchRequest,
    current_user: User = Depends(deps.get_current_active_user),
) -> GroupSearchResponse:
    group_service.get_accessible(group_id, current_user.id)
    results = retrieval_service.search(
        group_id=group_id,
        user_id=current_user.id,
        query=payload.query,
        limit=payload.limit,
        kinds=payload.kinds,
    )
    return GroupSearchResponse(group_id=group_id, results=results)


@router.post("/{group_id}/reindex", response_model=GroupReindexResponse, status_code=202)
def reindex_group_endpoint(
    group_id: int,
    current_user: User = Depends(deps.get_current_active_user),
) -> GroupReindexResponse:
    group_service.get_accessible(group_id, current_user.id)
    async_result = reindex_group.delay(group_id)
    return GroupReindexResponse(status="accepted", task_id=str(async_result.id))


@router.delete("/{group_id}", status_code=204)
def delete_group(
    group_id: int,
    current_user: User = Depends(deps.get_current_active_user),
) -> None:
    group_service.delete(group_id, current_user.id)
