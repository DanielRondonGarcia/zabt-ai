# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
import shutil
import os
from pathlib import Path
from fastapi import APIRouter, UploadFile, File, HTTPException, Depends
from sqlmodel import Session
from app.models import Meeting, MeetingRead, User
from app.api import deps

router = APIRouter()

UPLOAD_DIR = Path(os.environ.get("UPLOAD_DIR", "/media/uploads"))  # noqa: env override for non-docker dev
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

@router.post("/upload", response_model=MeetingRead)
async def upload_meeting(
    file: UploadFile = File(...),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    try:
        # Create a unique filename
        file_location = UPLOAD_DIR / file.filename

        # Save file to disk
        with open(file_location, "wb+") as file_object:
            shutil.copyfileobj(file.file, file_object)

        # Create DB entry
        meeting = Meeting(
            title=file.filename,
            file_path=str(file_location),
            owner_id=current_user.id,
            status="queued"
        )
        db.add(meeting)
        db.commit()
        db.refresh(meeting)

        return meeting


    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
