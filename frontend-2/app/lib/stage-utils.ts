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
  | "inferring_title"
  | "extracting_highlights"
  | "extracting_structured"
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
  "inferring_title",
  "extracting_highlights",
  "extracting_structured",
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
  inferring_title: "Inferring title…",
  extracting_highlights: "Extracting highlights…",
  extracting_structured: "Extracting structured output…",
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
    case "inferring_title":
      return "inferring_title";
    case "extracting_highlights":
      return "extracting_highlights";
    case "extracting_structured":
      return "extracting_structured";
    case "cleaning_up":
      return "done";
    default:
      return "uploaded";
  }
}

/**
 * Format safe audit metadata for one summary provider call.
 */
export function formatSummaryProgress(
  metadata: Record<string, unknown> | null | undefined
): string | null {
  if (!metadata) return null;

  const phase = metadata.phase;
  const blockIndex = metadata.block_index;
  const blockTotal = metadata.block_total;
  if (
    typeof blockIndex !== "number"
    || !Number.isSafeInteger(blockIndex)
    || typeof blockTotal !== "number"
    || !Number.isSafeInteger(blockTotal)
    || blockIndex < 1
    || blockTotal < 1
    || blockIndex > blockTotal
  ) {
    return null;
  }

  if (phase === "partial_chunk_summary" || phase === "single_chunk_summary") {
    return `Summary block ${blockIndex} of ${blockTotal}`;
  }
  if (phase === "summary_synthesis") {
    return `Final synthesis (${blockIndex} of ${blockTotal})`;
  }
  if (phase === "transcript_summary") {
    return `Transcript summary (${blockIndex} of ${blockTotal})`;
  }
  return null;
}

/**
 * Return the bounded LiteLLM correlation ID for one summary provider call.
 * Only client-generated opaque 32-character lowercase hex tokens are exposed;
 * anything else never reaches the audit UI.
 */
export function formatSummaryCorrelationId(
  metadata: Record<string, unknown> | null | undefined
): string | null {
  const value = metadata?.correlation_id;
  if (typeof value !== "string") return null;
  return /^[0-9a-f]{32}$/.test(value) ? value : null;
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
