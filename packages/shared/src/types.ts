// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2025-2026 Afeef Janjua
// Transcript primitives

export interface TranscriptWord {
  word: string;
  start: number;
  end: number;
}

export interface TranscriptSegment {
  start: number;
  end: number;
  speaker: string;
  text: string;
  words: TranscriptWord[];
}

export interface SpeakerBreakdown {
  percentage: number;
  name: string;
}

// Meeting

export type MediaType = "audio" | "video";

export interface Meeting {
  id: number;
  title: string;
  description: string | null;
  file_path: string;
  duration_seconds: number | null;
  created_at: string;
  status: MeetingStatus;
  /** Known pipeline stages plus bounded backend error text for failed meetings. */
  sub_status: MeetingSubStatus | (string & {}) | null;
  transcript_text: string | null;
  summary_text: string | null;
  original_summary_text: string | null;
  summary_edited: boolean;
  action_items_text: string | null;
  template_id: number | null;
  template_name: string | null;
  transcription_type: TranscriptionType;
  source_type: MeetingSource;
  source_url: string | null;
  youtube_title: string | null;
  youtube_duration_seconds: number | null;
  youtube_thumbnail_url: string | null;
  youtube_channel: string | null;
  audio_url: string | null;
  media_type: MediaType | null;
  speakers?: Record<string, SpeakerBreakdown>;
  segments?: TranscriptSegment[];
  meeting_type: MeetingType;
  structured_output: Record<string, unknown> | null;
  structured_output_status: StructuredOutputStatus;
  highlights: MeetingHighlight[];
  layout_hint: LayoutHint;
  requested_language: string | null;
  transliterated_text: string | null;
  /** Optional fields keep older clients compatible with the existing response. */
  visual_breakdown_status?: VisualBreakdownStatus | null;
  visual_breakdown_error?: string | null;
  visual_breakdown_completed_at?: string | null;
}

export interface VisualTranscriptLine {
  speaker: string | null;
  text: string;
  start: number;
  end: number;
}

export interface VisualSegment {
  id: number;
  sequence: number;
  start_time: number;
  end_time: number;
  screenshot_url: string;
  caption: string;
  confidence: number;
  transcript_lines: VisualTranscriptLine[];
}

export interface VisualBreakdownResponse {
  meeting_id: number;
  visual_breakdown_status: VisualBreakdownStatus | null;
  visual_breakdown_completed_at: string | null;
  visual_segments: VisualSegment[];
}

export interface MeetingList {
  items: Meeting[];
  total: number;
  skip: number;
  limit: number;
}

export interface MeetingHighlight {
  id: number;
  meeting_id: number;
  highlight_type: HighlightType;
  content: string;
  speaker: string | null;
  timestamp_start: number;
  timestamp_end: number | null;
  ai_answer: string | null;
  metadata: Record<string, unknown> | null;
  sort_order: number;
}

// Summary templates

export interface SummaryTemplate {
  id: number;
  name: string;
  body: string;
  template_type: TemplateType;
  is_system_default: boolean;
  owner_id: number | null;
  created_at: string;
  updated_at: string;
}

export interface SummaryTemplateListItem {
  id: number;
  name: string;
  template_type: TemplateType;
  is_system_default: boolean;
}

// User + Auth

export interface User {
  id: number;
  email: string;
  full_name: string | null;
  picture?: string | null;
  tier: UserTier;
  is_active: boolean;
  minutes_used_this_month: number;
}

export interface AuthToken {
  access_token: string;
  refresh_token: string;
  token_type: "bearer";
  expires_in: number;
  user: User;
}

export interface SSOLookupRequest {
  email: string;
}

export interface SSOLookupResponse {
  sso_enabled: boolean;
  redirect_url: string | null;
  organisation_name: string | null;
}

// Forward references — string literal types defined in enums.ts
import type {
  MeetingStatus,
  MeetingSubStatus,
  TranscriptionType,
  MeetingSource,
  MeetingType,
  StructuredOutputStatus,
  LayoutHint,
  HighlightType,
  TemplateType,
  UserTier,
  VisualBreakdownStatus,
} from "./enums";
