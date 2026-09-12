// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2025-2026 Afeef Janjua
import type { Meeting } from "@zabt/shared";

export type UserStage =
  | "uploaded"
  | "transcribing"
  | "aligning"
  | "diarizing"
  | "analyzing_video"
  | "building_context"
  | "summarizing"
  | "done"
  | "failed"
  | "visual_skipped"
  | "visual_fallback";

export type VisualOutcome =
  | "loading"
  | "success"
  | "skipped"
  | "fallback"
  | "error";

export const STAGE_ORDER: UserStage[] = [
  "uploaded",
  "transcribing",
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
  aligning: "Aligning…",
  diarizing: "Diarizing…",
  analyzing_video: "Analyzing video…",
  building_context: "Building context…",
  summarizing: "Summarizing…",
  done: "Done",
  failed: "Failed",
  visual_skipped: "Visual analysis skipped",
  visual_fallback: "Visual analysis unavailable",
};

export const VISUAL_OUTCOME_COPY = {
  skipped: {
    title: "Visual analysis skipped",
    description:
      "No visual context was available. The summary uses transcript-only evidence.",
  },
  fallback: {
    title: "Visual analysis unavailable",
    description:
      "Visual processing did not complete. The summary uses transcript-only evidence.",
  },
} as const;

export function getVisualOutcome(
  meeting: Pick<Meeting, "status" | "visual_breakdown_status">
): VisualOutcome | null {
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
    default:
      return null;
  }
}

export function getUserStage(
  meeting: Pick<Meeting, "status" | "sub_status" | "visual_breakdown_status">
): UserStage {
  if (meeting.status === "failed") return "failed";
  if (meeting.status === "completed") {
    const visualOutcome = getVisualOutcome(meeting);
    if (visualOutcome === "loading") return "analyzing_video";
    if (visualOutcome === "skipped") return "visual_skipped";
    if (visualOutcome === "fallback") return "visual_fallback";
    return "done";
  }
  if (meeting.status === "pending_upload" || meeting.status === "queued") {
    return "uploaded";
  }
  switch (meeting.sub_status) {
    case "downloading":
    case "downloading_youtube":
    case "validating":
    case "extracting_audio":
      return "uploaded";
    case "uploading":
    case "transcribing":
      return "transcribing";
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

export function isActiveMeeting(
  meeting: Pick<Meeting, "status"> &
    Partial<Pick<Meeting, "visual_breakdown_status">>
): boolean {
  return (
    meeting.status === "pending_upload" ||
    meeting.status === "queued" ||
    meeting.status === "processing" ||
    (meeting.status !== "failed" &&
      (meeting.visual_breakdown_status === "queued" ||
        meeting.visual_breakdown_status === "processing"))
  );
}
