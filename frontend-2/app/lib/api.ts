// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2025-2026 Afeef Janjua
import axios from "axios";
import { createApiClient } from "@zabt/shared";
import type {
  Meeting,
  MeetingHighlight,
  MeetingList,
  MeetingStatus,
  MeetingSubStatus,
  MeetingType,
  SpeakerBreakdown,
  SummaryTemplate,
  SummaryTemplateListItem,
  TranscriptSegment,
  TranscriptWord,
  User,
  VisualBreakdownResponse,
  VisualBreakdownStatus,
} from "@zabt/shared";

// Re-export for internal consumers that import from @/app/lib/api
export type {
  Meeting,
  MeetingHighlight,
  MeetingList,
  MeetingStatus,
  MeetingSubStatus,
  MeetingType,
  SpeakerBreakdown,
  SummaryTemplate,
  SummaryTemplateListItem,
  TranscriptSegment,
  TranscriptWord,
  User,
  VisualBreakdownResponse,
  VisualBreakdownStatus,
};

const API_URL =
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api/v1";

// The browser never receives an auth secret. The API owns both HttpOnly
// cookies, and this compatibility helper intentionally returns no bearer
// token for the shared client.
export const getToken = async (): Promise<string | null> => null;

const authClient = axios.create({
  baseURL: API_URL,
  withCredentials: true,
  headers: { "Content-Type": "application/json" },
});

export const clearToken = async (): Promise<void> => {
  await authClient.post("/auth/logout", { client: "web" });
};

// ── Axios client ──────────────────────────────────────────────────────────────

const apiClient = createApiClient({
  baseURL: API_URL,
  getAuthToken: getToken,
  refreshAuthToken: async () => {
    try {
      await authClient.post("/auth/refresh", { client: "web" });
      return true;
    } catch {
      return false;
    }
  },
  axiosConfig: { withCredentials: true },
  onUnauthorized: async () => {
    if (typeof window !== "undefined") {
      await clearToken().catch(() => undefined);
      window.location.href = "/login";
    }
  },
});

// ── Local email/password authentication ──────────────────────────────────────

export interface MeetingProcessingEvent {
  id: number;
  run_id: number;
  meeting_id: number;
  stage: string;
  event_type: "started" | "completed" | "failed" | "skipped" | (string & {});
  status: "started" | "completed" | "failed" | "skipped" | (string & {});
  task_id: string | null;
  message: string | null;
  error: string | null;
  metadata: Record<string, unknown> | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
}

export interface MeetingProcessingRun {
  id: number;
  meeting_id: number;
  owner_id: number;
  trigger: string;
  status: "queued" | "running" | "completed" | "failed" | (string & {});
  root_task_id: string | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  final_error: string | null;
  events: MeetingProcessingEvent[];
}

export interface MeetingProcessingAudit {
  meeting_id: number;
  runs: MeetingProcessingRun[];
}

export interface LocalAuthResponse {
  access_token: string | null;
  refresh_token: string | null;
  token_type: "bearer";
  expires_in: number;
  user: User;
}

export type AuthenticationMode = "local" | "microsoft_oidc";

export interface AuthenticationModeResponse {
  mode: AuthenticationMode;
  oidc_configured: boolean;
}

export interface AuthenticationModeConfiguration extends AuthenticationModeResponse {
  unlinked_user_count: number;
  unlinked_passwordless_user_count: number;
}

export const loginWithRememberMe = async (
  email: string,
  password: string,
  _rememberMe: boolean
): Promise<void> => {
  await authClient.post<LocalAuthResponse>("/auth/login", {
    email,
    password,
    client: "web",
  });
};

export const register = async (
  email: string,
  password: string,
  fullName: string
): Promise<void> => {
  await authClient.post<LocalAuthResponse>("/auth/register", {
    email,
    password,
    full_name: fullName,
    client: "web",
  });
};

export const login = async (email: string, password: string): Promise<void> => {
  await loginWithRememberMe(email, password, false);
};

export const loginSuperuser = async (email: string, password: string): Promise<void> => {
  await authClient.post<LocalAuthResponse>("/auth/superuser/login", { email, password });
};

export const getAuthenticationMode = async (): Promise<AuthenticationModeResponse> => {
  const { data } = await authClient.get<AuthenticationModeResponse>("/auth/mode");
  return data;
};

export const getAuthenticationModeConfiguration = async (): Promise<AuthenticationModeConfiguration> => {
  const { data } = await apiClient.get<AuthenticationModeConfiguration>("/auth/mode/config");
  return data;
};

export const updateAuthenticationMode = async (
  mode: AuthenticationMode,
): Promise<AuthenticationModeConfiguration> => {
  const { data } = await apiClient.put<AuthenticationModeConfiguration>(
    "/auth/mode/config",
    { mode },
  );
  return data;
};

export interface MicrosoftOidcStatus {
  configured: boolean;
  client_id: string | null;
  tenant: string | null;
  redirect_uri: string | null;
  scopes: string[];
  enabled: boolean;
  graph_configured: boolean;
  token_storage_configured: boolean;
}

export interface MicrosoftOidcChallenge {
  challenge_id: string;
  nonce: string;
}

export interface MicrosoftOidcConfiguration extends MicrosoftOidcStatus {
  can_manage: boolean;
  is_admin: boolean;
  is_superuser: boolean;
  created_at: string | null;
  updated_at: string | null;
  updated_by: number | null;
}

export interface MicrosoftOidcConfigurationUpdate {
  client_id: string;
  tenant: string;
  redirect_uri: string;
  enabled?: boolean;
}

export const getMicrosoftOidcStatus = async (): Promise<MicrosoftOidcStatus> => {
  const { data } = await authClient.get<MicrosoftOidcStatus>("/auth/microsoft/status");
  return data;
};

export const getMicrosoftOidcConfiguration = async (): Promise<MicrosoftOidcConfiguration> => {
  const { data } = await apiClient.get<MicrosoftOidcConfiguration>("/auth/microsoft/config");
  return data;
};

export const updateMicrosoftOidcConfiguration = async (
  configuration: MicrosoftOidcConfigurationUpdate,
): Promise<MicrosoftOidcConfiguration> => {
  const { data } = await apiClient.put<MicrosoftOidcConfiguration>(
    "/auth/microsoft/config",
    { ...configuration, enabled: configuration.enabled ?? true },
  );
  return data;
};

export const exchangeMicrosoftOidcToken = async (
  idToken: string,
  challengeId: string,
  client: "web" | "mobile" = "web",
): Promise<LocalAuthResponse> => {
  const { data } = await authClient.post<LocalAuthResponse>("/auth/microsoft/oidc/exchange", {
    id_token: idToken,
    challenge_id: challengeId,
    client,
  });
  return data;
};

export const createMicrosoftOidcChallenge = async (
  purpose: "login" | "link",
  client: "web" | "mobile" = "web",
): Promise<MicrosoftOidcChallenge> => {
  const requestClient = purpose === "link" ? apiClient : authClient;
  const { data } = await requestClient.post<MicrosoftOidcChallenge>(
    "/auth/microsoft/oidc/challenge",
    { purpose, client },
  );
  return data;
};

export const linkMicrosoftOidcToken = async (
  idToken: string,
  challengeId: string,
  client: "web" | "mobile" = "web",
): Promise<void> => {
  await apiClient.post("/auth/microsoft/oidc/link", {
    id_token: idToken,
    challenge_id: challengeId,
    client,
  });
};

// ── Groups and AI chat ────────────────────────────────────────────────────────

export type GroupAccessRole = "owner" | "editor" | "viewer";
export type GroupMemberRole = "editor" | "viewer";

export interface GroupSummary {
  id: number;
  name: string;
  description: string | null;
  owner_id: number;
  created_at: string;
  updated_at: string;
  access_role: GroupAccessRole;
  can_edit: boolean;
  can_manage_members: boolean;
  can_delete: boolean;
}

export interface GroupPayload {
  name: string;
  description?: string | null;
}

export interface AIChatSource {
  meeting_id: number;
  kind: string;
  chunk_index: number;
  score: number;
  text: string;
}

export type AIChatEvidenceStatus = "available" | "insufficient" | "not_required";

export interface AIChatResponse {
  conversation_id: number;
  group_id: number;
  answer: string;
  sources: AIChatSource[];
  evidence_status: AIChatEvidenceStatus;
}

export type AIChatMessageRole = "user" | "assistant";

export interface AIChatMessage {
  id: number;
  role: AIChatMessageRole;
  content: string;
  sources: AIChatSource[];
  evidence_status: AIChatEvidenceStatus | null;
  created_at: string;
}

export interface AIChatConversationSummary {
  id: number;
  group_id: number;
  title: string;
  created_at: string;
  updated_at: string;
  message_count: number;
}

export interface AIChatConversationDetail extends AIChatConversationSummary {
  /** Ordered oldest-first by the API. */
  messages: AIChatMessage[];
}

export interface GroupReindexResponse {
  status: "accepted";
  task_id: string;
}

export interface GroupUserSearchResult {
  user_id: number;
  email: string;
  full_name: string | null;
}

export interface GroupMember {
  user_id: number;
  email: string;
  full_name: string | null;
  role: GroupAccessRole;
  created_at: string;
  updated_at: string;
}

export interface McpTokenMetadata {
  id: number;
  label: string;
  token_prefix: string;
  created_at: string;
  last_used_at: string | null;
  expires_at: string;
  revoked_at: string | null;
}

export interface McpTokenCreated extends McpTokenMetadata {
  token: string;
}

export interface McpStatus {
  enabled: boolean;
  endpoint: string;
  auth_mode: "bearer_token";
  tools: string[];
}

export interface AskAiChatPayload {
  groupId: number;
  message: string;
  limit?: number;
  /** Omit to start a new conversation; the response carries the created id. */
  conversationId?: number;
}

/** Returns the HTTP status of a failed API call, or `undefined` for network/unknown errors. */
export const getApiErrorStatus = (error: unknown): number | undefined =>
  axios.isAxiosError(error) ? error.response?.status : undefined;

export const getApiErrorMessage = (error: unknown): string | undefined => {
  if (!axios.isAxiosError(error)) return undefined;
  const detail = error.response?.data?.detail;
  if (typeof detail === "string") return detail;
  if (typeof detail === "object" && detail !== null && "message" in detail) {
    const message = (detail as { message?: unknown }).message;
    return typeof message === "string" ? message : undefined;
  }
  return undefined;
};

export const getGroups = async (): Promise<GroupSummary[]> => {
  const { data } = await apiClient.get<GroupSummary[]>("/groups/");
  return data;
};

export const getGroup = async (groupId: number): Promise<GroupSummary> => {
  const { data } = await apiClient.get<GroupSummary>(`/groups/${groupId}`);
  return data;
};

export const createGroup = async (payload: GroupPayload): Promise<GroupSummary> => {
  const { data } = await apiClient.post<GroupSummary>("/groups/", payload);
  return data;
};

export const updateGroup = async (
  groupId: number,
  payload: Partial<GroupPayload>,
): Promise<GroupSummary> => {
  const { data } = await apiClient.patch<GroupSummary>(`/groups/${groupId}`, payload);
  return data;
};

export const deleteGroup = async (groupId: number): Promise<void> => {
  await apiClient.delete(`/groups/${groupId}`);
};

export const reindexGroup = async (groupId: number): Promise<GroupReindexResponse> => {
  const { data } = await apiClient.post<GroupReindexResponse>(`/groups/${groupId}/reindex`);
  return data;
};

export const searchGroupUsers = async (
  groupId: number,
  query: string,
  limit = 10,
): Promise<GroupUserSearchResult[]> => {
  const { data } = await apiClient.get<GroupUserSearchResult[]>(
    `/groups/${groupId}/members/search`,
    { params: { q: query, limit } },
  );
  return data;
};

export const listGroupMembers = async (groupId: number): Promise<GroupMember[]> => {
  const { data } = await apiClient.get<GroupMember[]>(`/groups/${groupId}/members`);
  return data;
};

export const addGroupMember = async (
  groupId: number,
  userId: number,
  role: GroupMemberRole,
): Promise<GroupMember> => {
  const { data } = await apiClient.post<GroupMember>(`/groups/${groupId}/members`, {
    user_id: userId,
    role,
  });
  return data;
};

export const updateGroupMember = async (
  groupId: number,
  userId: number,
  role: GroupMemberRole,
): Promise<GroupMember> => {
  const { data } = await apiClient.patch<GroupMember>(
    `/groups/${groupId}/members/${userId}`,
    { role },
  );
  return data;
};

export const removeGroupMember = async (groupId: number, userId: number): Promise<void> => {
  await apiClient.delete(`/groups/${groupId}/members/${userId}`);
};

export const getMcpStatus = async (): Promise<McpStatus> => {
  const { data } = await apiClient.get<McpStatus>("/mcp/status");
  return data;
};

export const getMcpTokens = async (): Promise<McpTokenMetadata[]> => {
  const { data } = await apiClient.get<McpTokenMetadata[]>("/mcp/tokens");
  return data;
};

export const createMcpToken = async (
  label: string,
  expiresInDays: number,
): Promise<McpTokenCreated> => {
  const { data } = await apiClient.post<McpTokenCreated>("/mcp/tokens", {
    label,
    expires_in_days: expiresInDays,
  });
  return data;
};

export const revokeMcpToken = async (tokenId: number): Promise<void> => {
  await apiClient.delete(`/mcp/tokens/${tokenId}`);
};

export const assignMeetingGroup = async (
  meetingId: number,
  groupId: number | null,
): Promise<Meeting> => {
  const { data } = await apiClient.patch<Meeting>(
    `/meetings/${meetingId}/assign-group`,
    { group_id: groupId },
  );
  return data;
};

export interface MeetingReindexResponse {
  status: "accepted";
  task_id: string;
}

export const reindexMeeting = async (meetingId: number): Promise<MeetingReindexResponse> => {
  const { data } = await apiClient.post<MeetingReindexResponse>(`/meetings/${meetingId}/reindex`);
  return data;
};

export const askAiChat = async ({
  groupId,
  message,
  limit,
  conversationId,
}: AskAiChatPayload): Promise<AIChatResponse> => {
  const { data } = await apiClient.post<AIChatResponse>("/ai-chat/", {
    group_id: groupId,
    message,
    ...(limit !== undefined ? { limit } : {}),
    ...(conversationId !== undefined ? { conversation_id: conversationId } : {}),
  });
  return data;
};

export const listAiChatConversations = async (
  groupId: number,
): Promise<AIChatConversationSummary[]> => {
  const { data } = await apiClient.get<AIChatConversationSummary[]>("/ai-chat/conversations", {
    params: { group_id: groupId },
  });
  return data;
};

export const getAiChatConversation = async (
  conversationId: number,
): Promise<AIChatConversationDetail> => {
  const { data } = await apiClient.get<AIChatConversationDetail>(
    `/ai-chat/conversations/${conversationId}`,
  );
  return data;
};

export const deleteAiChatConversation = async (conversationId: number): Promise<void> => {
  await apiClient.delete(`/ai-chat/conversations/${conversationId}`);
};

// ── Styles ────────────────────────────────────────────────────────────────────

export const uploadStyle = async (file: File): Promise<string[]> => {
  const formData = new FormData();
  formData.append("files", file);
  const res = await apiClient.post<string[]>("/styles/upload", formData, {
    headers: { "Content-Type": "multipart/form-data" },
  });
  return res.data;
};

export const getStyles = async (): Promise<string[]> => {
  try {
    const res = await apiClient.get<string[]>("/styles/");
    return res.data;
  } catch {
    return [];
  }
};

// ── Meetings ──────────────────────────────────────────────────────────────────

interface PresignedUploadResponse {
  upload_url: string;
  file_key: string;
  storage_provider: string;
}

export const uploadMeeting = async (
  file: File,
  title: string = "Untitled Meeting",
  description?: string
): Promise<Meeting> => {
  // 1. Get Presigned URL
  const { data: presignedData } = await apiClient.post<PresignedUploadResponse>("/meetings/presigned-upload", {
    filename: file.name,
    content_type: file.type || "audio/mpeg"
  });

  // 2. Create the meeting record (before upload so webhook can find it)
  const { data: meeting } = await apiClient.post<Meeting>("/meetings/", {
    title,
    description,
    file_key: presignedData.file_key,
    content_type: file.type || "audio/mpeg",
  });

  // 3. Upload directly to S3/MinIO
  await axios.put(presignedData.upload_url, file, {
    headers: {
      "Content-Type": file.type || "audio/mpeg"
    }
  });

  // 4. For S3/R2: confirm upload to trigger pipeline (MinIO uses webhooks instead)
  if (presignedData.storage_provider === "s3") {
    await apiClient.post(`/meetings/${meeting.id}/confirm-upload`);
  }

  return meeting;
};

export const submitYoutubeUrl = async (url: string, transcriptionType: string = "general"): Promise<Meeting> => {
  const res = await apiClient.post<Meeting>("/meetings/youtube", { url, transcription_type: transcriptionType });
  return res.data;
};

export const getMeetings = async (
  skip = 0,
  limit = 20,
  groupId?: number,
): Promise<Meeting[]> => {
  const res = await apiClient.get<Meeting[]>("/meetings/", {
    params: {
      skip,
      limit,
      ...(groupId !== undefined ? { group_id: groupId } : {}),
    },
  });
  return res.data;
};


export const getMeeting = async (id: number): Promise<Meeting> => {
  const res = await apiClient.get<Meeting>(`/meetings/${id}`);
  return res.data;
};

export const getMeetingProcessingAudit = async (meetingId: number): Promise<MeetingProcessingAudit> => {
  const res = await apiClient.get<MeetingProcessingAudit>(`/meetings/${meetingId}/processing-audit`);
  return res.data;
};

export const reprocessMeeting = async (meetingId: number): Promise<Meeting> => {
  const res = await apiClient.post<Meeting>(`/meetings/${meetingId}/reprocess`);
  return res.data;
};

export const deleteMeeting = async (id: number): Promise<void> => {
  await apiClient.delete(`/meetings/${id}`);
};

export const updateMeetingSummary = async (
  meetingId: number,
  summaryText: string
): Promise<{ id: number; summary_text: string; original_summary_text: string | null; summary_edited: boolean }> => {
  const res = await apiClient.patch(`/meetings/${meetingId}/summary`, {
    summary_text: summaryText,
  });
  return res.data;
};

export const restoreMeetingSummary = async (
  meetingId: number
): Promise<{ id: number; summary_text: string; original_summary_text: string | null; summary_edited: boolean }> => {
  const res = await apiClient.post(`/meetings/${meetingId}/summary/restore`);
  return res.data;
};

export const resummarizeMeeting = async (
  meetingId: number,
  templateId?: number
): Promise<void> => {
  await apiClient.post(`/meetings/${meetingId}/summarize`, {
    template_id: templateId ?? null,
  });
};

/** Fetch visual segments without introducing a visual viewer into the meeting page. */
export const getVisualSegments = async (
  meetingId: number
): Promise<VisualBreakdownResponse> => {
  const res = await apiClient.get<VisualBreakdownResponse>(
    `/meetings/${meetingId}/visual-segments`
  );
  return res.data;
};

// ── Templates ─────────────────────────────────────────────────────────────────

export const getTemplates = async (): Promise<SummaryTemplate[]> => {
  const res = await apiClient.get<SummaryTemplate[]>("/templates/");
  return res.data;
};

export const getTemplate = async (id: number): Promise<SummaryTemplate> => {
  const res = await apiClient.get<SummaryTemplate>(`/templates/${id}`);
  return res.data;
};

export const createTemplate = async (
  name: string,
  body: string
): Promise<SummaryTemplate> => {
  const res = await apiClient.post<SummaryTemplate>("/templates/", { name, body });
  return res.data;
};

export const updateTemplate = async (
  id: number,
  name: string,
  body: string
): Promise<SummaryTemplate> => {
  const res = await apiClient.put<SummaryTemplate>(`/templates/${id}`, { name, body });
  return res.data;
};

export const deleteTemplate = async (id: number): Promise<void> => {
  await apiClient.delete(`/templates/${id}`);
};

export const setDefaultTemplate = async (
  id: number
): Promise<{ default_template_id: number; default_template_name: string }> => {
  const res = await apiClient.post(`/templates/${id}/set-default`);
  return res.data;
};

// ── PDF Export ───────────────────────────────────────────────────────────────

export const exportPdf = async (
  meetingId: number,
  type: "summary" | "transcript"
): Promise<void> => {
  const res = await apiClient.get(`/meetings/${meetingId}/export/pdf`, {
    params: { type },
    responseType: "blob",
  });

  // Parse filename from Content-Disposition header, fallback to default
  const disposition = res.headers["content-disposition"] || "";
  const filenameMatch = disposition.match(/filename="?([^"]+)"?/);
  const filename = filenameMatch?.[1] || `meeting-${type}.pdf`;

  // Create a temporary object URL and trigger download
  const blob = new Blob([res.data], { type: "application/pdf" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
};

// ── Integrations ────────────────────────────────────────────────────────────

export interface IntegrationRead {
  id: number;
  provider: "microsoft" | "google";
  provider_email: string | null;
  status: "active" | "expired" | "revoked";
  connected_at: string;
  scopes: string[];
}

export interface CalendarEventRead {
  id: number;
  provider: string;
  external_event_id: string;
  title: string;
  start_time: string;
  end_time: string;
  conferencing_platform: "teams" | "meet" | "zoom" | null;
  join_url: string | null;
  organizer_email: string | null;
  attendees: { email: string; name: string }[];
  auto_join: boolean;
  bot_status: string;
  meeting_id: number | null;
}

export const getIntegrations = async (): Promise<IntegrationRead[]> => {
  const { data } = await apiClient.get("/integrations/");
  return data;
};

export const connectProvider = async (provider: string): Promise<{ auth_url: string }> => {
  const { data } = await apiClient.post(`/integrations/${provider}/connect`);
  return data;
};

export const disconnectProvider = async (provider: string): Promise<void> => {
  await apiClient.delete(`/integrations/${provider}`);
};

export const getCalendarEvents = async (): Promise<CalendarEventRead[]> => {
  const { data } = await apiClient.get("/integrations/calendar/events");
  return data;
};

export const updateCalendarEvent = async (
  eventId: number,
  updates: { auto_join?: boolean }
): Promise<CalendarEventRead> => {
  const { data } = await apiClient.patch(`/integrations/calendar/events/${eventId}`, updates);
  return data;
};

// ── Email Sharing ───────────────────────────────────────────────────────────

export interface EmailShareRead {
  id: number;
  meeting_id: number;
  recipients: { email: string; name: string; status: string }[];
  status: "pending" | "sent" | "partially_failed" | "failed";
  sent_at: string | null;
  created_at: string;
}

export const shareMeetingViaEmail = async (
  meetingId: number,
  recipientEmails: string[]
): Promise<EmailShareRead> => {
  const { data } = await apiClient.post(`/meetings/${meetingId}/share-email`, {
    recipient_emails: recipientEmails,
  });
  return data;
};

export const getMeetingShares = async (
  meetingId: number
): Promise<EmailShareRead[]> => {
  const { data } = await apiClient.get(`/meetings/${meetingId}/shares`);
  return data;
};

// ── Meeting Intelligence ──────────────────────────────────────────────────────

export async function updateMeetingType(meetingId: number, meetingType: MeetingType) {
  return apiClient.patch(`/meetings/${meetingId}/meeting-type`, { meeting_type: meetingType });
}

export async function reExtractIntelligence(meetingId: number) {
  return apiClient.post(`/meetings/${meetingId}/re-extract`);
}

export async function getMeetingHighlights(meetingId: number, highlightType?: string) {
  const params = highlightType ? { highlight_type: highlightType } : {};
  return apiClient.get<MeetingHighlight[]>(`/meetings/${meetingId}/highlights`, { params });
}

export async function fetchCurrentUser(): Promise<User> {
  const { data } = await apiClient.get<User>("/users/me");
  return data;
}

// ── User AI provider ─────────────────────────────────────────────────────────

export type CustomAIProvider = "openai" | "anthropic" | "ollama";

export interface AIProviderConfiguration {
  provider: CustomAIProvider | null;
  model: string | null;
  base_url: string | null;
  api_key_configured: boolean;
  enabled: boolean;
  use_for_summary: boolean;
  use_for_chat: boolean;
}

export interface UpdateAIProviderConfiguration {
  provider: CustomAIProvider;
  model: string;
  base_url?: string;
  api_key?: string;
  enabled: boolean;
  use_for_summary: boolean;
  use_for_chat: boolean;
}

export interface DiscoverAIProviderModelsRequest {
  provider: CustomAIProvider;
  base_url?: string;
  api_key?: string;
}

export type AIProviderModelCatalogSource =
  | "openai-compatible"
  | "anthropic"
  | "ollama-compatible"
  | "ollama-tags";

export interface AIProviderModelCatalog {
  models: string[];
  source: AIProviderModelCatalogSource;
}

export async function getAIProviderConfiguration(): Promise<AIProviderConfiguration> {
  const { data } = await apiClient.get<AIProviderConfiguration>("/users/me/ai-provider");
  return data;
}

export async function updateAIProviderConfiguration(
  payload: UpdateAIProviderConfiguration,
): Promise<AIProviderConfiguration> {
  const { data } = await apiClient.patch<AIProviderConfiguration>(
    "/users/me/ai-provider",
    payload,
  );
  return data;
}

export async function discoverAIProviderModels(
  payload: DiscoverAIProviderModelsRequest,
): Promise<AIProviderModelCatalog> {
  const { data } = await apiClient.post<AIProviderModelCatalog>(
    "/users/me/ai-provider/models",
    payload,
  );
  return data;
}

export async function deleteAIProviderConfiguration(): Promise<void> {
  await apiClient.delete("/users/me/ai-provider");
}

// ── Languages ─────────────────────────────────────────────────────────────────

export type LanguageEntry = {
  code: string;
  display_name: string;
  whisper_lang: string;
  script: string;
  transliterate_from: string | null;
  is_default: boolean;
};

export async function listLanguages(): Promise<LanguageEntry[]> {
  const { data } = await apiClient.get<LanguageEntry[]>("/languages");
  return data;
}

export async function getLanguagePreferences(): Promise<string[]> {
  const { data } = await apiClient.get<{ codes: string[] }>(
    "/users/me/language-preferences"
  );
  return data.codes;
}

export async function setLanguagePreferences(codes: string[]): Promise<string[]> {
  const { data } = await apiClient.put<{ codes: string[] }>(
    "/users/me/language-preferences",
    { codes }
  );
  return data.codes;
}

export async function reTranscribeMeeting(
  meetingId: number,
  language: string
): Promise<void> {
  await apiClient.post(`/meetings/${meetingId}/re-transcribe`, { language });
}

export default apiClient;
