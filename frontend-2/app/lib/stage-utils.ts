// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2025-2026 Afeef Janjua
import type { Meeting } from "@/app/lib/api";

export type UserStage =
  | "uploaded"
  | "transcribing"
  | "transcription_skipped"
  | "aligning"
  | "diarizing"
  | "analyzing_video"
  | "building_context"
  | "summarizing"
  | "done"
  | "failed"
  | "cancelled";

export type VisualOutcome =
  | "loading"
  | "success"
  | "skipped"
  | "fallback"
  | "cancelled"
  | "error";

export const STAGE_ORDER: UserStage[] = [
  "uploaded",
  "transcribing",
  "transcription_skipped",
  "aligning",
  "diarizing",
  "analyzing_video",
  "building_context",
  "summarizing",
  "done",
];

export const STAGE_LABELS: Record<UserStage, string> = {
  uploaded: "Uploaded",
  transcribing: "Transcribing…",
  transcription_skipped: "No audio track; continuing…",
  aligning: "Aligning…",
  diarizing: "Diarizing…",
  analyzing_video: "Analyzing video…",
  building_context: "Building context…",
  summarizing: "Summarizing…",
  done: "Done",
  failed: "Failed",
  cancelled: "Cancelled",
};

export function getVisualOutcome(
  meeting: Pick<Meeting, "status" | "visual_breakdown_status">
): VisualOutcome | null {
  if (meeting.status === "cancelled") return null;
  if (meeting.status === "failed") return "error";

  switch (meeting.visual_breakdown_status) {
    case "queued":
    case "processing":
      return "loading";
    case "completed":
      return "success";
    case "skipped":
      return "skipped";
    case "fallback":
      return "fallback";
    case "cancelled":
      return "cancelled";
    case "failed":
      return "error";
    default:
      return null;
  }
}

/**
 * Map a Meeting's `status` + `sub_status` to a user-visible stage.
 */
export function getUserStage(meeting: Pick<Meeting, "status" | "sub_status">): UserStage {
  if (meeting.status === "cancelled") return "cancelled";
  if (meeting.status === "failed") return "failed";
  if (meeting.status === "completed") return "done";
  if (meeting.status === "pending_upload" || meeting.status === "queued") return "uploaded";

  // status === "processing"
  switch (meeting.sub_status) {
    case "downloading":
    case "downloading_youtube":
    case "validating":
    case "extracting_audio":
      return "uploaded";
    case "uploading":
    case "transcribing":
    case "preparing_audio":
    case "transcribing_chunk":
      return "transcribing";
    case "transcription_skipped_no_audio":
      return "transcription_skipped";
    case "aligning":
      return "aligning";
    case "diarizing":
    case "parsing":
      return "diarizing";
    case "analyzing_video":
      return "analyzing_video";
    case "building_context":
      return "building_context";
    case "summarizing":
      return "summarizing";
    case "cleaning_up":
      return "done";
    default:
      return "uploaded";
  }
}

/**
 * Get the index of a stage in the pipeline order (0-based).
 * Returns -1 for terminal stages that are not in the linear pipeline.
 */
export function getStageIndex(stage: UserStage): number {
  const idx = STAGE_ORDER.indexOf(stage);
  return idx;
}

/**
 * Check if a meeting is in an active (processing) state.
 */
export function isActiveMeeting(meeting: Pick<Meeting, "status">): boolean {
  return meeting.status === "pending_upload"
    || meeting.status === "queued"
    || meeting.status === "processing";
}
