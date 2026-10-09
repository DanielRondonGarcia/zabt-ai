# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.api import deps
from app.models import (
    GroupCreate,
    GroupMemberRead,
    GroupRead,
    GroupUpdate,
    GroupUserSearchRead,
    User,
)
from app.services.group import (
    GroupMemberRecord,
    GroupReindexEnqueueError,
    group_service,
)
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


class GroupMemberRequest(BaseModel):
    user_id: int = Field(..., gt=0)
    role: Literal["viewer", "editor"] = "viewer"


class GroupMemberRoleRequest(BaseModel):
    role: Literal["viewer", "editor"]


class _LazyReindexGroupTask:
    def delay(self, group_id: int):
        from app.worker import reindex_group as worker_reindex_group

        return worker_reindex_group.delay(group_id)


reindex_group = _LazyReindexGroupTask()


def _member_read(record: GroupMemberRecord) -> GroupMemberRead:
    return GroupMemberRead(
        user_id=record.user.id,
        email=record.user.email,
        full_name=record.user.full_name,
        role=record.role,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


@router.get("/", response_model=list[GroupRead])
def list_groups(
    current_user: User = Depends(deps.get_current_active_user),
) -> Any:
    return [
        group_service.to_read(access)
        for access in group_service.list_accessible_with_roles(current_user.id)
    ]


@router.post("/", response_model=GroupRead, status_code=201)
def create_group(
    *,
    payload: GroupCreate,
    current_user: User = Depends(deps.get_current_active_user),
) -> Any:
    group = group_service.create(current_user.id, payload.name, payload.description)
    return group_service.to_read(group_service.get_access(group.id, current_user.id))


@router.get("/{group_id}", response_model=GroupRead)
def get_group(
    group_id: int,
    current_user: User = Depends(deps.get_current_active_user),
) -> Any:
    return group_service.to_read(group_service.get_access(group_id, current_user.id))


@router.patch("/{group_id}", response_model=GroupRead)
def update_group(
    *,
    group_id: int,
    payload: GroupUpdate,
    current_user: User = Depends(deps.get_current_active_user),
) -> Any:
    try:
        group = group_service.update(
            group_id,
            current_user.id,
            payload.name,
            payload.description,
            description_provided="description" in payload.model_fields_set,
        )
    except GroupReindexEnqueueError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "group_reindex_pending",
                "group_id": exc.group_id,
                "message": (
                    "The group was updated, but its AI index is pending. "
                    "Retry the group reindex operation."
                ),
            },
        ) from exc
    return group_service.to_read(group_service.get_access(group.id, current_user.id))


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
    group_service.require_editor(group_id, current_user.id)
    async_result = reindex_group.delay(group_id)
    return GroupReindexResponse(status="accepted", task_id=str(async_result.id))


@router.get("/{group_id}/members/search", response_model=list[GroupUserSearchRead])
def search_group_users(
    group_id: int,
    q: str = Query(..., min_length=2, max_length=100),
    limit: int = Query(default=10, ge=1, le=20),
    current_user: User = Depends(deps.get_current_active_user),
) -> list[GroupUserSearchRead]:
    return [
        GroupUserSearchRead(user_id=user_id, email=email, full_name=full_name)
        for user_id, email, full_name in group_service.search_users(
            group_id,
            current_user.id,
            q,
            limit,
        )
    ]


@router.get("/{group_id}/members", response_model=list[GroupMemberRead])
def list_group_members(
    group_id: int,
    current_user: User = Depends(deps.get_current_active_user),
) -> list[GroupMemberRead]:
    return [
        _member_read(record)
        for record in group_service.list_members(group_id, current_user.id)
    ]


@router.post(
    "/{group_id}/members",
    response_model=GroupMemberRead,
    status_code=status.HTTP_201_CREATED,
)
def add_group_member(
    group_id: int,
    payload: GroupMemberRequest,
    current_user: User = Depends(deps.get_current_active_user),
) -> GroupMemberRead:
    return _member_read(
        group_service.add_member(
            group_id,
            current_user.id,
            payload.user_id,
            payload.role,
        )
    )


@router.patch("/{group_id}/members/{user_id}", response_model=GroupMemberRead)
def update_group_member(
    group_id: int,
    user_id: int,
    payload: GroupMemberRoleRequest,
    current_user: User = Depends(deps.get_current_active_user),
) -> GroupMemberRead:
    return _member_read(
        group_service.update_member(
            group_id,
            current_user.id,
            user_id,
            payload.role,
        )
    )


@router.delete("/{group_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_group_member(
    group_id: int,
    user_id: int,
    current_user: User = Depends(deps.get_current_active_user),
) -> None:
    group_service.remove_member(group_id, current_user.id, user_id)


@router.delete("/{group_id}", status_code=204)
def delete_group(
    group_id: int,
    current_user: User = Depends(deps.get_current_active_user),
) -> None:
    group_service.delete(group_id, current_user.id)
