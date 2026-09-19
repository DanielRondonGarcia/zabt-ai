# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
from typing import List, Optional
from fastapi import HTTPException, status
from sqlmodel import Session, select
from app.db.engine import engine
from app.models import Group, User
from app.services.base import BaseService


class GroupService(BaseService):
    def list_for_user(self, user_id: int) -> List[Group]:
        with Session(engine) as session:
            statement = select(Group).where(Group.owner_id == user_id)
            return list(session.exec(statement).all())

    def get_accessible(self, group_id: int, user_id: int) -> Group:
        with Session(engine) as session:
            group = session.get(Group, group_id)
            if group is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found.")
            if group.owner_id != user_id:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied.")
            return group

    def create(self, user_id: int, name: str, description: Optional[str] = None) -> Group:
        self._validate_name(name)
        self._validate_description(description)
        group = Group(
            name=name,
            description=description,
            owner_id=user_id,
        )
        return self.save(group)

    def update(self, group_id: int, user_id: int, name: Optional[str] = None, description: Optional[str] = None) -> Group:
        if name is not None:
            self._validate_name(name)
        if description is not None:
            self._validate_description(description)

        with Session(engine) as session:
            group = session.get(Group, group_id)
            if group is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found.")
            if group.owner_id != user_id:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied.")

            if name is not None:
                group.name = name
            if description is not None:
                group.description = description

            session.add(group)
            session.commit()
            session.refresh(group)
            return group

    def delete(self, group_id: int, user_id: int) -> None:
        with Session(engine) as session:
            group = session.get(Group, group_id)
            if group is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found.")
            if group.owner_id != user_id:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied.")
            try:
                from app.worker import delete_group_vectors

                delete_group_vectors.delay(group_id)
            except Exception:
                # Vector cleanup is failure-isolated from group deletion.
                pass
            session.delete(group)
            session.commit()

    @staticmethod
    def _validate_name(name: Optional[str]) -> None:
        if name is None or not name.strip():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Group name cannot be empty.")
        if len(name) > 100:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Group name must not exceed 100 characters.")

    @staticmethod
    def _validate_description(description: Optional[str]) -> None:
        if description is not None and len(description) > 500:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Group description must not exceed 500 characters.")


group_service = GroupService()
