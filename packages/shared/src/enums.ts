// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2025-2026 Afeef Janjua
// String literal union types (compile-time only; no runtime cost)

export type MeetingStatus =
  | "pending_upload"
  | "queued"
  | "processing"
  | "completed"
  | "failed";

/** Known pipeline sub-statuses. Error messages may still use arbitrary strings. */
export type MeetingSubStatus =
  | "downloading"
  | "downloading_youtube"
  | "validating"
  | "extracting_audio"
  | "uploading"
  | "transcribing"
  | "aligning"
  | "diarizing"
  | "parsing"
  | "analyzing_video"
  | "building_context"
  | "summarizing"
  | "cleaning_up";

/** Terminal states for the optional visual breakdown stage. */
export type VisualBreakdownStatus =
  | "queued"
  | "processing"
  | "completed"
  | "skipped"
  | "fallback";

export type TranscriptionType = "general" | "medical";

export type MeetingSource = "upload" | "youtube" | "record";

export type MeetingType =
  | "generic"
  | "grooming"
  | "standup"
  | "retro"
  | "one_on_one";

export type StructuredOutputStatus =
  | "pending"
  | "processing"
  | "completed"
  | "failed";

export type LayoutHint = "cards" | "table" | "columns" | "list";

export type HighlightType =
  | "action_item"
  | "key_question"
  | "topic"
  | "chapter";

export type TemplateType = "built_in" | "custom";

export type UserTier = "free" | "pro" | "enterprise";

export type OAuthProvider = "google" | "microsoft";

// Runtime arrays for validation / iteration (use these when you need a list)

export const MEETING_STATUSES: readonly MeetingStatus[] = [
  "pending_upload",
  "queued",
  "processing",
  "completed",
  "failed",
] as const;

export const MEETING_SUB_STATUSES: readonly MeetingSubStatus[] = [
  "downloading",
  "downloading_youtube",
  "validating",
  "extracting_audio",
  "uploading",
  "transcribing",
  "aligning",
  "diarizing",
  "parsing",
  "analyzing_video",
  "building_context",
  "summarizing",
  "cleaning_up",
] as const;

export const VISUAL_BREAKDOWN_STATUSES: readonly VisualBreakdownStatus[] = [
  "queued",
  "processing",
  "completed",
  "skipped",
  "fallback",
] as const;

export const TRANSCRIPTION_TYPES: readonly TranscriptionType[] = [
  "general",
  "medical",
] as const;

export const OAUTH_PROVIDERS: readonly OAuthProvider[] = [
  "google",
  "microsoft",
] as const;
