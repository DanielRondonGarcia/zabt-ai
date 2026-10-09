# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

from fastapi import HTTPException, status
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.db.engine import engine
from app.models import (
    Group,
    GroupMemberRole,
    GroupMembership,
    GroupRead,
    Meeting,
    User,
)
from app.services.base import BaseService

logger = logging.getLogger(__name__)


class GroupReindexEnqueueError(RuntimeError):
    """The group update committed, but its vector reindex was not queued."""

    def __init__(self, group_id: int):
        super().__init__(f"Vector reindex could not be queued for group {group_id}.")
        self.group_id = group_id


@dataclass(frozen=True)
class GroupAccess:
    group: Group
    role: str


@dataclass(frozen=True)
class GroupMemberRecord:
    user: User
    role: str
    created_at: datetime
    updated_at: datetime


class GroupService(BaseService):
    def list_accessible_with_roles(self, user_id: int) -> list[GroupAccess]:
        """Return owned and directly shared groups with computed access roles."""

        with Session(engine) as session:
            statement = (
                select(Group, GroupMembership.role)
                .outerjoin(
                    GroupMembership,
                    (GroupMembership.group_id == Group.id)
                    & (GroupMembership.user_id == user_id),
                )
                .where(
                    or_(
                        Group.owner_id == user_id,
                        GroupMembership.user_id == user_id,
                    )
                )
                .order_by(Group.created_at.asc(), Group.id.asc())
            )
            return [
                GroupAccess(
                    group=group,
                    role="owner" if group.owner_id == user_id else self._role_value(role),
                )
                for group, role in session.exec(statement).all()
            ]

    def list_for_user(self, user_id: int) -> List[Group]:
        return [access.group for access in self.list_accessible_with_roles(user_id)]

    def list_for_user_with_meeting_counts(
        self, user_id: int
    ) -> list[tuple[Group, int]]:
        """Return accessible groups and their meeting counts in bounded queries."""

        accessible = self.list_accessible_with_roles(user_id)
        if not accessible:
            return []

        group_ids = [access.group.id for access in accessible]
        with Session(engine) as session:
            count_statement = (
                select(Meeting.group_id, func.count(Meeting.id))
                .where(Meeting.group_id.in_(group_ids))
                .group_by(Meeting.group_id)
            )
            counts = {
                group_id: int(count)
                for group_id, count in session.exec(count_statement).all()
            }
        return [
            (access.group, counts.get(access.group.id, 0)) for access in accessible
        ]

    def get_access(self, group_id: int, user_id: int) -> GroupAccess:
        with Session(engine) as session:
            group = session.get(Group, group_id)
            if group is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Group not found.",
                )
            if group.owner_id == user_id:
                return GroupAccess(group=group, role="owner")

            membership = session.exec(
                select(GroupMembership).where(
                    GroupMembership.group_id == group_id,
                    GroupMembership.user_id == user_id,
                )
            ).first()
            if membership is None:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Access denied.",
                )
            return GroupAccess(
                group=group,
                role=self._role_value(membership.role),
            )

    def get_accessible(self, group_id: int, user_id: int) -> Group:
        return self.get_access(group_id, user_id).group

    def require_editor(self, group_id: int, user_id: int) -> Group:
        access = self.get_access(group_id, user_id)
        if access.role not in {"owner", GroupMemberRole.EDITOR.value}:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Editor access is required.",
            )
        return access.group

    def require_owner(self, group_id: int, user_id: int) -> Group:
        access = self.get_access(group_id, user_id)
        if access.role != "owner":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only the group owner can manage members.",
            )
        return access.group

    @staticmethod
    def to_read(access: GroupAccess) -> GroupRead:
        is_editor = access.role in {"owner", GroupMemberRole.EDITOR.value}
        is_owner = access.role == "owner"
        group = access.group
        return GroupRead(
            id=group.id,
            name=group.name,
            description=group.description,
            owner_id=group.owner_id,
            created_at=group.created_at,
            updated_at=group.updated_at,
            access_role=access.role,
            can_edit=is_editor,
            can_manage_members=is_owner,
            can_delete=is_owner,
        )

    def create(self, user_id: int, name: str, description: Optional[str] = None) -> Group:
        self._validate_name(name)
        self._validate_description(description)
        group = Group(
            name=name,
            description=description,
            owner_id=user_id,
        )
        return self.save(group)

    def update(
        self,
        group_id: int,
        user_id: int,
        name: Optional[str] = None,
        description: Optional[str] = None,
        *,
        description_provided: bool = False,
    ) -> Group:
        if name is not None:
            self._validate_name(name)
        description_was_provided = description_provided or description is not None
        if description_was_provided:
            self._validate_description(description)

        introduction_changed = False
        with Session(engine) as session:
            group = session.get(Group, group_id)
            if group is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Group not found.",
                )

            access = self._get_access_in_session(session, group, user_id)
            if access.role not in {"owner", GroupMemberRole.EDITOR.value}:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Editor access is required.",
                )

            changed = False
            if name is not None and group.name != name:
                group.name = name
                changed = True
            if description_was_provided and group.description != description:
                group.description = description
                introduction_changed = True
                changed = True
            if changed:
                group.updated_at = datetime.utcnow()

            session.add(group)
            session.commit()
            session.refresh(group)

        if introduction_changed:
            self._enqueue_group_reindex(group_id)
        return group

    def delete(self, group_id: int, user_id: int) -> None:
        with Session(engine) as session:
            group = session.get(Group, group_id)
            if group is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Group not found.",
                )
            if group.owner_id != user_id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Access denied.",
                )
            try:
                from app.worker import delete_group_vectors

                delete_group_vectors.delay(group_id)
            except Exception:
                # Vector cleanup is failure-isolated from group deletion.
                pass
            session.delete(group)
            session.commit()

    def search_users(
        self, group_id: int, owner_id: int, query: str, limit: int = 10
    ) -> list[tuple[int, str, Optional[str]]]:
        self.require_owner(group_id, owner_id)
        normalized = query.strip()
        if len(normalized) < 2:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Search must contain at least 2 characters.",
            )
        limit = min(max(limit, 1), 20)
        escaped_query = (
            normalized.replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
        )
        pattern = f"%{escaped_query}%"
        with Session(engine) as session:
            existing_ids = list(
                session.exec(
                    select(GroupMembership.user_id).where(
                        GroupMembership.group_id == group_id
                    )
                ).all()
            )
            statement = (
                select(User.id, User.email, User.full_name)
                .where(
                    User.is_active.is_(True),
                    User.id != owner_id,
                    or_(
                        User.email.ilike(pattern, escape="\\"),
                        func.coalesce(User.full_name, "").ilike(pattern, escape="\\"),
                    ),
                )
                .order_by(
                    func.coalesce(User.full_name, "").asc(),
                    User.email.asc(),
                )
                .limit(limit)
            )
            if existing_ids:
                statement = statement.where(User.id.not_in(existing_ids))
            return [
                (user_id, email, full_name)
                for user_id, email, full_name in session.exec(statement).all()
            ]

    def list_members(self, group_id: int, owner_id: int) -> list[GroupMemberRecord]:
        group = self.require_owner(group_id, owner_id)
        with Session(engine) as session:
            records: list[GroupMemberRecord] = []
            owner = session.get(User, group.owner_id)
            if owner is not None:
                records.append(
                    GroupMemberRecord(
                        user=owner,
                        role="owner",
                        created_at=group.created_at,
                        updated_at=group.updated_at,
                    )
                )

            statement = (
                select(GroupMembership, User)
                .join(User, User.id == GroupMembership.user_id)
                .where(GroupMembership.group_id == group_id)
                .order_by(func.coalesce(User.full_name, "").asc(), User.email.asc())
            )
            records.extend(
                GroupMemberRecord(
                    user=user,
                    role=self._role_value(membership.role),
                    created_at=membership.created_at,
                    updated_at=membership.updated_at,
                )
                for membership, user in session.exec(statement).all()
            )
            return records

    def add_member(
        self,
        group_id: int,
        owner_id: int,
        user_id: int,
        role: str,
    ) -> GroupMemberRecord:
        group = self.require_owner(group_id, owner_id)
        member_role = self._validate_role(role)
        if user_id == group.owner_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="The group owner is already a member.",
            )

        with Session(engine) as session:
            user = session.get(User, user_id)
            if user is None or not user.is_active:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Active user not found.",
                )
            existing = session.exec(
                select(GroupMembership).where(
                    GroupMembership.group_id == group_id,
                    GroupMembership.user_id == user_id,
                )
            ).first()
            if existing is not None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="This user already has access to the group.",
                )
            membership = GroupMembership(
                group_id=group_id,
                user_id=user_id,
                role=member_role,
            )
            session.add(membership)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="This user already has access to the group.",
                ) from None
            session.refresh(membership)
            session.refresh(user)
            return GroupMemberRecord(
                user=user,
                role=self._role_value(membership.role),
                created_at=membership.created_at,
                updated_at=membership.updated_at,
            )

    def update_member(
        self,
        group_id: int,
        owner_id: int,
        user_id: int,
        role: str,
    ) -> GroupMemberRecord:
        group = self.require_owner(group_id, owner_id)
        member_role = self._validate_role(role)
        if user_id == group.owner_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="The group owner role cannot be changed.",
            )

        with Session(engine) as session:
            membership = session.exec(
                select(GroupMembership).where(
                    GroupMembership.group_id == group_id,
                    GroupMembership.user_id == user_id,
                )
            ).first()
            if membership is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Group member not found.",
                )
            user = session.get(User, user_id)
            membership.role = member_role
            membership.updated_at = datetime.utcnow()
            session.add(membership)
            session.commit()
            session.refresh(membership)
            if user is not None:
                session.refresh(user)
            return GroupMemberRecord(
                user=user,
                role=self._role_value(membership.role),
                created_at=membership.created_at,
                updated_at=membership.updated_at,
            )

    def remove_member(self, group_id: int, owner_id: int, user_id: int) -> None:
        group = self.require_owner(group_id, owner_id)
        if user_id == group.owner_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="The group owner cannot be removed.",
            )
        with Session(engine) as session:
            membership = session.exec(
                select(GroupMembership).where(
                    GroupMembership.group_id == group_id,
                    GroupMembership.user_id == user_id,
                )
            ).first()
            if membership is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Group member not found.",
                )
            session.delete(membership)
            session.commit()

    @staticmethod
    def _role_value(role: GroupMemberRole | str | None) -> str:
        if isinstance(role, GroupMemberRole):
            return role.value
        return str(role) if role is not None else "viewer"

    @staticmethod
    def _validate_role(role: str) -> GroupMemberRole:
        try:
            return GroupMemberRole(role)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Role must be viewer or editor.",
            ) from None

    @staticmethod
    def _get_access_in_session(
        session: Session, group: Group, user_id: int
    ) -> GroupAccess:
        if group.owner_id == user_id:
            return GroupAccess(group=group, role="owner")
        membership = session.exec(
            select(GroupMembership).where(
                GroupMembership.group_id == group.id,
                GroupMembership.user_id == user_id,
            )
        ).first()
        if membership is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied.",
            )
        return GroupAccess(
            group=group,
            role=GroupService._role_value(membership.role),
        )

    @staticmethod
    def _enqueue_group_reindex(group_id: int) -> None:
        try:
            from app.worker import reindex_group

            reindex_group.delay(group_id)
        except Exception as exc:
            logger.exception(
                "group reindex enqueue failed group_id=%s; database update is durable",
                group_id,
            )
            raise GroupReindexEnqueueError(group_id) from exc

    @staticmethod
    def _validate_name(name: Optional[str]) -> None:
        if name is None or not name.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Group name cannot be empty.",
            )
        if len(name) > 100:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Group name must not exceed 100 characters.",
            )

    @staticmethod
    def _validate_description(description: Optional[str]) -> None:
        if description is not None and len(description) > 500:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Group introduction must not exceed 500 characters.",
            )


group_service = GroupService()
