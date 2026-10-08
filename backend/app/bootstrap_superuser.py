# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""Provision the deployment-owned system superuser after database migrations."""

import sys

from sqlmodel import Session

from app.db.engine import engine
from app.services.system_superuser import (
    SystemSuperuserBootstrapError,
    ensure_system_superuser,
)


def main() -> int:
    try:
        with Session(engine) as db:
            ensure_system_superuser(db)
    except SystemSuperuserBootstrapError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
