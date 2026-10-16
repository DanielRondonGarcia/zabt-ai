// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2025-2026 Afeef Janjua
import type {
  Meeting,
  MeetingProcessingAudit,
  MeetingProcessingRun,
  User,
} from "@zabt/shared";

export interface MobileMeetingProcessingAudit extends MeetingProcessingAudit {
  current_run_id: number | null;
}

const MEETING_STATUSES = new Set([
  "pending_upload",
  "queued",
  "processing",
  "completed",
  "failed",
  "cancelled",
]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isValidId(value: unknown): value is number {
  return Number.isSafeInteger(value) && (value as number) > 0;
}

export function getCancelableProcessingRun(
  meeting: Pick<Meeting, "id" | "owner_id" | "status"> | undefined,
  audit: MobileMeetingProcessingAudit | undefined,
  currentUser: Pick<User, "id"> | undefined,
): MeetingProcessingRun | null {
  if (
    !meeting ||
    !currentUser ||
    !isValidId(meeting.id) ||
    !isValidId(meeting.owner_id) ||
    !isValidId(currentUser.id) ||
    meeting.owner_id !== currentUser.id ||
    typeof meeting.status !== "string" ||
    !MEETING_STATUSES.has(meeting.status) ||
    meeting.status === "cancelled"
  ) {
    return null;
  }

  const rawAudit: unknown = audit;
  if (
    !isRecord(rawAudit) ||
    rawAudit.meeting_id !== meeting.id ||
    !isValidId(rawAudit.current_run_id) ||
    !Array.isArray(rawAudit.runs)
  ) {
    return null;
  }

  let newestActiveRun: MeetingProcessingRun | null = null;
  let newestCreatedAt = Number.NEGATIVE_INFINITY;
  const runIds = new Set<number>();

  for (const candidate of rawAudit.runs) {
    if (
      !isRecord(candidate) ||
      !isValidId(candidate.id) ||
      !isValidId(candidate.meeting_id) ||
      !isValidId(candidate.owner_id) ||
      typeof candidate.status !== "string" ||
      typeof candidate.created_at !== "string"
    ) {
      return null;
    }

    const createdAt = Date.parse(candidate.created_at);
    if (
      candidate.meeting_id !== meeting.id ||
      candidate.owner_id !== currentUser.id ||
      !Number.isFinite(createdAt) ||
      runIds.has(candidate.id)
    ) {
      return null;
    }
    runIds.add(candidate.id);

    if (candidate.status !== "queued" && candidate.status !== "running") {
      continue;
    }

    if (
      createdAt > newestCreatedAt ||
      (createdAt === newestCreatedAt &&
        candidate.id > (newestActiveRun?.id ?? 0))
    ) {
      newestActiveRun = candidate as unknown as MeetingProcessingRun;
      newestCreatedAt = createdAt;
    }
  }

  if (!newestActiveRun || newestActiveRun.id !== rawAudit.current_run_id) {
    return null;
  }

  return newestActiveRun;
}
