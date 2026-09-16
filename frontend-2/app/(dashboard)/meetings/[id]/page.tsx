// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2025-2026 Afeef Janjua
"use client";

import { useState, useEffect, useRef, use } from "react";
import { useRouter } from "next/navigation";
import { usePostHog } from "posthog-js/react";
import { getMeeting, updateMeetingSummary, restoreMeetingSummary, Meeting, updateMeetingType, reExtractIntelligence, listLanguages, type LanguageEntry } from "@/app/lib/api";
import type { MeetingType } from "@/app/lib/api";
import { ReTranscribeDialog } from "@/app/components/ReTranscribeDialog";
import { StatusBadge } from "@/app/components/status-badge";
import { Button } from "@/app/components/ui/button";
import { Tabs, TabsList, TabsTrigger } from "@/app/components/ui/tabs";
import { TranscriptViewer } from "@/app/components/transcript-viewer";
import { StickyMediaPlayer } from "@/app/components/sticky-media-player";
import { PaywallModal } from "@/app/components/paywall-modal";
import { getUserStage, STAGE_LABELS, isActiveMeeting } from "@/app/lib/stage-utils";
import { ProgressSteps } from "@/app/components/ui/progress-steps";
import { TemplateSelector } from "@/app/components/template-selector";
import { Spinner } from "@/app/components/ui/spinner";
import { SummaryEditor } from "@/app/components/summary-editor";
import { exportPdf } from "@/app/lib/api";
import { ShareEmailDialog } from "@/app/components/share-email-dialog";
import { ActionItemsList } from "@/app/components/action-items-list";
import { KeyQuestionsList } from "@/app/components/key-questions-list";
import { ChaptersList } from "@/app/components/chapters-list";
import { StructuredOutputRenderer } from "@/app/components/structured-output-renderer";
import { MeetingTypeSelector } from "@/app/components/meeting-type-selector";
import { useTranscriptStore } from "@/app/lib/use-transcript-store";
import { Separator } from "@/app/components/ui/separator";
import { Pencil, Download, Mail, Copy, FileText, FileDown, ChevronDown, Eye, RotateCcw, RefreshCw, Loader2, Languages, UserRound } from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/app/components/ui/dropdown-menu";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

const ROMAN_SPEAKER_COLORS: Record<string, string> = {
  SPEAKER_00: "bg-stone-500 text-white",
  SPEAKER_01: "bg-stone-600 text-white",
  SPEAKER_02: "bg-stone-700 text-white",
  SPEAKER_03: "bg-stone-400 text-stone-950",
};
const romanSpeakerColor = (s: string) =>
  ROMAN_SPEAKER_COLORS[s] ?? "bg-stone-200 text-stone-500";
const romanSpeakerLabel = (s: string) => {
  if (s === "SPEAKER_UNKNOWN") return "Unknown Speaker";
  const n = s.split("_")[1];
  return n !== undefined ? `Speaker ${parseInt(n) + 1}` : s;
};

type RomanRow = { speaker: string | null; text: string };

function parseRomanRows(text: string): RomanRow[] {
  // LLM preserves [SPEAKER_XX] markers from the source transcript. Split on
  // those boundaries so each speaker turn becomes its own row.
  const raw = text
    .split(/\n+/)
    .map((l) => l.trim())
    .filter(Boolean);
  const rows: RomanRow[] = [];
  for (const line of raw) {
    const m = /^\[(SPEAKER_[A-Z0-9_]+)\]\s*(.*)$/i.exec(line);
    if (m) {
      rows.push({ speaker: m[1].toUpperCase(), text: m[2].trim() });
    } else if (rows.length > 0) {
      // Continuation of the previous speaker's turn
      rows[rows.length - 1].text += (rows[rows.length - 1].text ? " " : "") + line;
    } else {
      rows.push({ speaker: null, text: line });
    }
  }
  return rows.filter((r) => r.text);
}

export default function MeetingDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const router = useRouter();
  const posthog = usePostHog();
  const summaryViewedFiredRef = useRef(false);
  const [meeting, setMeeting] = useState<Meeting | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<"summary" | "transcript" | "structured">("summary");
  const [isEditing, setIsEditing] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [showOriginal, setShowOriginal] = useState(false);
  const [showShareDialog, setShowShareDialog] = useState(false);
  const [reTranscribeOpen, setReTranscribeOpen] = useState(false);
  const [langCatalog, setLangCatalog] = useState<LanguageEntry[]>([]);
  const [transcriptView, setTranscriptView] = useState<"original" | "roman">("original");

  const { setSeekRequest } = useTranscriptStore();
  const resetTranscriptMedia = useTranscriptStore((state) => state.reset);

  // Stub for user tier logic
  const isFreeTier = false;
  const mediaIdentity = meeting
    ? `${meeting.id}:${meeting.audio_url ?? meeting.file_path ?? ""}:${meeting.media_type ?? "audio"}`
    : null;
  const shouldMountMediaPlayer = Boolean(
    meeting
      && meeting.id === Number(id)
      && activeTab === "transcript"
      && meeting.status === "completed"
      && meeting.segments?.length
      && (meeting.audio_url || meeting.file_path)
  );

  useEffect(() => {
    resetTranscriptMedia(mediaIdentity);
    return () => resetTranscriptMedia(null);
  }, [id, mediaIdentity, resetTranscriptMedia]);

  useEffect(() => {
    if (!shouldMountMediaPlayer) {
      resetTranscriptMedia(null);
    }
  }, [resetTranscriptMedia, shouldMountMediaPlayer]);

  const handleMeetingTypeChange = async (newType: MeetingType) => {
    if (!meeting) return;
    try {
      await updateMeetingType(meeting.id, newType);
      setMeeting((m) => m ? { ...m, meeting_type: newType } : m);
    } catch (err) {
      console.error("Failed to update meeting type:", err);
    }
  };

  const handleRetryExtraction = async () => {
    if (!meeting) return;
    try {
      await reExtractIntelligence(meeting.id);
    } catch (err) {
      console.error("Failed to retry extraction:", err);
    }
  };

  useEffect(() => {
    listLanguages().then(setLangCatalog).catch(() => {});
  }, []);

  const langDisplayName = (code: string | null | undefined) =>
    code ? langCatalog.find((e) => e.code === code)?.display_name ?? code : null;

  const elapsedRef = useRef(0);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    const load = async () => {
      try {
        // Here we would typically fetch the real meeting, but to fulfill T004 we can also 
        // inject the mock data if this is the test meeting ID (123 or 999).
        // For development, we'll try to fetch, and if it fails or if we want to force mock:
        let data: Meeting;
        try {
          data = await getMeeting(Number(id));
        } catch (e) {
          // Fallback to mock data for Phase 1/2 UI testing if ID is 999
          if (id === "999") {
            const mockRes = await fetch("/mocks/transcript_mock.json");
            data = await mockRes.json();
          } else {
            throw e;
          }
        }

        setMeeting(data);
        setLoading(false);
        if (isActiveMeeting(data)) startPolling();
      } catch {
        // Fallback for UI testing
        setError("Meeting not found or you don't have access to it.");
        setLoading(false);
      }
    };
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
    return () => stopPolling();
  }, [id]);

  const startPolling = () => {
    elapsedRef.current = 0;
    intervalRef.current = setInterval(async () => {
      elapsedRef.current += 3;
      try {
        const data = await getMeeting(Number(id));
        setMeeting(data);
        if (!isActiveMeeting(data)) stopPolling();
        else if (elapsedRef.current >= 30 && intervalRef.current) {
          clearInterval(intervalRef.current);
          intervalRef.current = setInterval(async () => {
            const refreshed = await getMeeting(Number(id));
            setMeeting(refreshed);
            if (!isActiveMeeting(refreshed)) stopPolling();
          }, 10000);
        }
      } catch {
        stopPolling();
      }
    }, 3000);
  };

  const stopPolling = () => {
    if (intervalRef.current) {
      clearInterval(intervalRef.current);
      intervalRef.current = null;
    }
  };

  // Warn before navigating away with unsaved edits
  useEffect(() => {
    if (!isEditing) return;
    const handler = (e: BeforeUnloadEvent) => {
      e.preventDefault();
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [isEditing]);

  const handleSaveSummary = async (markdown: string) => {
    if (!meeting) return;
    setIsSaving(true);
    try {
      const result = await updateMeetingSummary(meeting.id, markdown);
      setMeeting((m) =>
        m
          ? {
              ...m,
              summary_text: result.summary_text,
              original_summary_text: result.original_summary_text,
              summary_edited: result.summary_edited,
            }
          : m
      );
      setIsEditing(false);
      posthog?.capture("summary_edited", { meeting_id: meeting.id });
    } catch {
      // Could add error toast here in the future
    } finally {
      setIsSaving(false);
    }
  };

  const handleRestoreSummary = async () => {
    if (!meeting) return;
    try {
      const result = await restoreMeetingSummary(meeting.id);
      setMeeting((m) =>
        m
          ? {
              ...m,
              summary_text: result.summary_text,
              original_summary_text: result.original_summary_text,
              summary_edited: result.summary_edited,
            }
          : m
      );
      setShowOriginal(false);
      posthog?.capture("summary_restored", { meeting_id: meeting.id });
    } catch {
      // Could add error toast here in the future
    }
  };

  if (loading) {
    return (
      <div className="px-6 py-12 flex items-center justify-center">
        <div className="flex flex-col items-center gap-3">
          <Spinner className="size-6 text-muted-foreground" />
          <p className="text-sm text-muted-foreground font-medium">Loading meeting…</p>
        </div>
      </div>
    );
  }

  if (error || !meeting) {
    return (
      <div className="px-6 py-12 text-center">
        <p className="text-red-600 mb-4">{error ?? "Meeting not found."}</p>
        <Button onClick={() => router.push("/")}>Back to home</Button>
      </div>
    );
  }

  const isActive = isActiveMeeting(meeting);
  const userStage = getUserStage(meeting);
  const visualFallback = meeting.visual_breakdown_status === "fallback"
    || meeting.visual_breakdown_status === "skipped";
  const formattedDate = new Date(meeting.created_at).toLocaleString('en-US', {
    weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: 'numeric'
  });

  const formatDuration = (seconds: number | null) => {
    if (!seconds) return "Unknown duration";
    const m = Math.floor(seconds / 60);
    return `${m} min`;
  };

  const transcriptSegmentCount = meeting.segments?.length ?? 0;
  const hasTranscriptText = Boolean(meeting.transcript_text?.trim());

  return (
    <div className="relative flex flex-col h-full overflow-hidden">
      {/* Fixed header + tabs area */}
      <div className="flex-shrink-0 px-6 pt-6 pb-0 space-y-3 border-b border-border bg-background">
        {/* Row 1: Title + Status + Meeting Type */}
        <div className="flex items-start justify-between gap-4">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-3 mb-1">
              <h1 className="text-lg font-semibold text-foreground truncate">{meeting.title}</h1>
              <StatusBadge status={meeting.status} subStatus={meeting.sub_status} />
            </div>
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              {meeting.speakers && Object.keys(meeting.speakers).length > 0 && (
                <>
                  <span className="text-foreground/70 font-medium">
                    {Object.values(meeting.speakers).map((s) => s.name).join(", ")}
                  </span>
                  <span className="text-border">·</span>
                </>
              )}
              <span>{formattedDate}</span>
              <span className="text-border">·</span>
              <span>{formatDuration(meeting.duration_seconds)}</span>
              {meeting.transcription_type === "medical" && (
                <>
                  <span className="text-border">·</span>
                  <span className="text-xs font-medium text-primary">Medical</span>
                </>
              )}
              {meeting.requested_language && (
                <>
                  <span className="text-border">·</span>
                  <span className="rounded-4xl bg-stone-100 px-3 py-1 text-xs font-medium text-stone-700">
                    Transcribed as: {langDisplayName(meeting.requested_language)}
                  </span>
                </>
              )}
            </div>
          </div>
        </div>

        {/* Meeting-wide status banners — rendered above tabs so they're visible regardless of active tab */}
        {isActive && (
          <div className="bg-amber-50 border border-amber-200 rounded-lg px-5 py-4 text-sm text-amber-800 space-y-3">
            <div>
              <div className="flex items-center gap-2 mb-1">
                <Loader2 className="size-4 animate-spin text-amber-600" />
                <p className="font-medium">
                   {meeting.status === "processing" && meeting.sub_status
                     ? STAGE_LABELS[userStage]
                    : "Processing your meeting…"}
                </p>
              </div>
              <p className="text-amber-700">
                This page will update automatically. Transcription and summarization may take a few
                minutes depending on file length.
              </p>
            </div>
             <ProgressSteps currentStage={userStage} />
          </div>
        )}

        {meeting.status === "failed" && (
          <div className="bg-red-50 border border-red-200 rounded-lg px-5 py-4 text-sm text-red-800">
            <p className="font-medium mb-1">Processing failed</p>
            <p className="text-red-700">
              {meeting.sub_status || "The AI pipeline encountered an error for this meeting. Partial results may be available below."}
            </p>
          </div>
        )}

        {/* Row 2: Tabs only — clean, no action buttons here */}
        <Tabs
          value={activeTab}
          onValueChange={(value) => {
            setActiveTab(value as "summary" | "transcript" | "structured");
            if (value === "summary" && !summaryViewedFiredRef.current && meeting) {
              posthog?.capture('summary_viewed', { meeting_id: meeting.id });
              summaryViewedFiredRef.current = true;
            }
          }}
        >
          <TabsList>
            <TabsTrigger value="summary">Summary</TabsTrigger>
            <TabsTrigger value="transcript">Transcript</TabsTrigger>
            <TabsTrigger value="structured">Structured Output</TabsTrigger>
          </TabsList>
        </Tabs>
      </div>

      {/* Scrollable content area */}
      <div className="flex-1 overflow-y-auto">
        <div className="px-6 py-6 space-y-4">
          {/* Summary Tab Content */}
          {activeTab === "summary" && (
            <div className="space-y-4">
              {/* Summary Toolbar — uniform bar with all actions */}
              {meeting.status === "completed" && !isEditing && !showOriginal && (
                <div className="flex items-center gap-1 rounded-lg bg-muted/50 border border-border px-1.5 py-1">
                  <Button variant="ghost" size="sm" onClick={() => setIsEditing(true)} disabled={isActive}>
                    <Pencil className="size-3.5" />
                    Edit
                  </Button>
                  <Separator orientation="vertical" className="h-4 mx-0.5" />
                  <DropdownMenu>
                    <DropdownMenuTrigger render={<Button variant="ghost" size="sm" disabled={isActive} />}>
                      <Download className="size-3.5" />
                      Export
                      <ChevronDown className="size-3" />
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="start">
                      <DropdownMenuItem onClick={() => { navigator.clipboard.writeText(meeting.summary_text!); }}>
                        <Copy className="size-3.5" />
                        Copy to clipboard
                      </DropdownMenuItem>
                      <DropdownMenuItem onClick={() => {
                        const blob = new Blob([meeting.summary_text!], { type: "text/plain" });
                        const url = URL.createObjectURL(blob);
                        const a = document.createElement("a");
                        a.href = url; a.download = `${meeting.title}-summary.txt`; a.click();
                        URL.revokeObjectURL(url);
                      }}>
                        <FileText className="size-3.5" />
                        Download text
                      </DropdownMenuItem>
                      <DropdownMenuItem onClick={async () => {
                        try { await exportPdf(meeting.id, "summary"); posthog?.capture('summary_exported', { meeting_id: meeting.id }); }
                        catch { alert("Failed to download PDF."); }
                      }}>
                        <FileDown className="size-3.5" />
                        Download PDF
                      </DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                  <Separator orientation="vertical" className="h-4 mx-0.5" />
                  <TemplateSelector
                    meetingId={meeting.id}
                    currentTemplateId={meeting.template_id}
                    currentTemplateName={meeting.template_name}
                    onResummarizeStarted={() => {
                      setMeeting((m) => m ? { ...m, status: "processing", sub_status: "summarizing" } : m);
                      startPolling();
                    }}
                    disabled={isActive}
                  />
                  {meeting.summary_edited && meeting.original_summary_text && (
                    <>
                      <Separator orientation="vertical" className="h-4 mx-0.5" />
                      <Button variant="ghost" size="sm" onClick={() => setShowOriginal(true)}>
                        <Eye className="size-3.5" />
                        View original
                      </Button>
                    </>
                  )}
                  <div className="flex-1" />
                  <Separator orientation="vertical" className="h-4 mx-0.5" />
                  <Button variant="ghost" size="sm" onClick={() => setShowShareDialog(true)}>
                    <Mail className="size-3.5" />
                    Share
                  </Button>
                </div>
              )}
              {/* Viewing original — show restore bar */}
              {meeting.status === "completed" && !isEditing && showOriginal && (
                <div className="flex items-center gap-1 rounded-lg bg-muted/50 border border-border px-1.5 py-1">
                  <Button variant="ghost" size="sm" onClick={() => setShowOriginal(false)}>
                    <Eye className="size-3.5" />
                    View current
                  </Button>
                  <Separator orientation="vertical" className="h-4 mx-0.5" />
                  <Button variant="ghost" size="sm" className="text-primary" onClick={handleRestoreSummary}>
                    <RotateCcw className="size-3.5" />
                    Restore original
                  </Button>
                </div>
              )}

              {/* Editor or read-only content */}
              {isEditing ? (
                <SummaryEditor
                  initialContent={meeting.summary_text ?? ""}
                  onSave={handleSaveSummary}
                  onCancel={() => setIsEditing(false)}
                  saving={isSaving}
                />
              ) : showOriginal && meeting.original_summary_text ? (
                <div className="bg-stone-50 rounded-lg border border-stone-200 p-6">
                  <p className="text-xs font-medium text-stone-400 uppercase tracking-wide mb-4">Original AI Summary</p>
                  <div className="prose prose-stone max-w-none prose-headings:font-semibold prose-h1:text-xl prose-h2:text-lg prose-h3:text-base prose-p:text-stone-700 prose-p:leading-relaxed">
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>{meeting.original_summary_text}</ReactMarkdown>
                  </div>
                </div>
              ) : meeting.summary_text ? (
                <div className="bg-white rounded-lg border border-stone-200 p-6">
                  <div className="prose prose-stone max-w-none prose-headings:font-semibold prose-h1:text-xl prose-h2:text-lg prose-h3:text-base prose-p:text-stone-700 prose-p:leading-relaxed">
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>{meeting.summary_text}</ReactMarkdown>
                  </div>
                </div>
              ) : (
                <div className="bg-white rounded-lg border border-stone-200 p-6">
                  <p className="text-sm text-stone-400 italic">No summary available yet.</p>
                  {meeting.status === "completed" && (
                    <Button
                      variant="ghost"
                      size="sm"
                      className="mt-3 text-primary"
                      onClick={() => setIsEditing(true)}
                    >
                      <Pencil className="size-3.5" />
                      Write a summary
                    </Button>
                  )}
                </div>
              )}

              {meeting.action_items_text && (
                <section className="bg-white rounded-lg border border-stone-200 p-6">
                  <div className="prose prose-stone max-w-none prose-headings:font-semibold prose-h1:text-xl prose-h2:text-lg prose-h3:text-base prose-p:text-stone-700 prose-p:leading-relaxed">
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>{meeting.action_items_text}</ReactMarkdown>
                  </div>
                </section>
              )}

              {visualFallback && (
                <div className="rounded-lg border border-stone-200 bg-stone-50 px-4 py-3 text-sm text-stone-600">
                  Visual context was unavailable for this meeting. The summary uses transcript-only
                  evidence and remains available.
                </div>
              )}

              {meeting.highlights && meeting.highlights.length > 0 && (
                <div className="space-y-4">
                  {meeting.highlights.some((h) => h.highlight_type === "action_item") && (
                    <div className="bg-white rounded-lg border border-stone-200 p-6">
                      <ActionItemsList
                        highlights={meeting.highlights}
                        onTimestampClick={(seconds) => setSeekRequest(seconds)}
                      />
                    </div>
                  )}
                  {meeting.highlights.some((h) => h.highlight_type === "key_question") && (
                    <div className="bg-white rounded-lg border border-stone-200 p-6">
                      <KeyQuestionsList
                        highlights={meeting.highlights}
                        onTimestampClick={(seconds) => setSeekRequest(seconds)}
                      />
                    </div>
                  )}
                  {meeting.highlights.some((h) => h.highlight_type === "chapter") && (
                    <div className="bg-white rounded-lg border border-stone-200 p-6">
                      <ChaptersList
                        highlights={meeting.highlights}
                        onTimestampClick={(seconds) => setSeekRequest(seconds)}
                      />
                    </div>
                  )}
                </div>
              )}
            </div>
          )}

          {/* Structured Output Tab Content */}
          {activeTab === "structured" && (
            <div className="space-y-4">
              {/* Structured output toolbar — same bar style */}
              {meeting.status === "completed" && (
                <div className="flex items-center gap-1 rounded-lg bg-muted/50 border border-border px-1.5 py-1">
                  <MeetingTypeSelector
                    value={(meeting.meeting_type as MeetingType) || "generic"}
                    onChange={handleMeetingTypeChange}
                    size="sm"
                  />
                  <Separator orientation="vertical" className="h-4 mx-0.5" />
                  <Button variant="ghost" size="sm" onClick={handleRetryExtraction}>
                    <RefreshCw className="size-3.5" />
                    Re-extract
                  </Button>
                </div>
              )}
              <div className="bg-white rounded-lg border border-stone-200 p-6">
                <StructuredOutputRenderer
                  data={meeting.structured_output || null}
                  status={(meeting.structured_output_status as any) || "pending"}
                  layoutHint={(meeting.layout_hint as any) || "list"}
                  meetingType={(meeting.meeting_type as MeetingType) || "generic"}
                  onRetry={handleRetryExtraction}
                />
              </div>
            </div>
          )}

          {/* Transcript Tab Content */}
          {activeTab === "transcript" && (
            <div className="space-y-4">
              {/* Transcript toolbar — wraps so actions remain usable on narrow screens */}
              {meeting.status === "completed" && meeting.segments && meeting.segments.length > 0 && (
                <div className="flex flex-wrap items-center gap-1 rounded-lg border border-border bg-muted/50 px-1.5 py-1">
                  <Button variant="ghost" size="sm" onClick={async () => {
                    try { await exportPdf(meeting.id, "transcript"); posthog?.capture("transcript_exported", { meeting_id: meeting.id }); }
                    catch { alert("Failed to download PDF."); }
                  }}>
                    <FileDown className="size-3.5" />
                    Download PDF
                  </Button>
                  <div className="flex-1" />
                  {/* Roman script toggle — only shown when transliteration is available */}
                  {meeting.transliterated_text && (
                    <div role="group" aria-label="Transcript script" className="inline-flex gap-1 rounded-lg border border-border bg-background p-1 mr-1">
                      <button
                        type="button"
                        aria-pressed={transcriptView === "original"}
                        onClick={() => setTranscriptView("original")}
                        className={`rounded-lg px-3 py-1 text-sm font-medium transition-colors motion-reduce:transition-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/50 ${
                          transcriptView === "original"
                            ? "bg-foreground text-background"
                            : "text-muted-foreground hover:bg-accent"
                        }`}
                      >
                        Original
                      </button>
                      <button
                        type="button"
                        aria-pressed={transcriptView === "roman"}
                        onClick={() => setTranscriptView("roman")}
                        className={`rounded-lg px-3 py-1 text-sm font-medium transition-colors motion-reduce:transition-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/50 ${
                          transcriptView === "roman"
                            ? "bg-foreground text-background"
                            : "text-muted-foreground hover:bg-accent"
                        }`}
                      >
                        Roman
                      </button>
                    </div>
                  )}
                  <Button variant="ghost" size="sm" onClick={() => setShowShareDialog(true)}>
                    <Mail className="size-3.5" />
                    Share
                  </Button>
                  <Separator orientation="vertical" className="h-4 mx-0.5" />
                  <Button variant="ghost" size="sm" onClick={() => setReTranscribeOpen(true)} disabled={isActive}>
                    Re-transcribe
                  </Button>
                </div>
              )}
              <div className={`grid min-w-0 gap-4 lg:gap-6 ${shouldMountMediaPlayer ? "lg:grid-cols-[minmax(0,1.65fr)_minmax(20rem,0.85fr)]" : "lg:grid-cols-1"}`}>
                {shouldMountMediaPlayer && (
                  <section aria-labelledby="meeting-recording-heading" className="min-w-0 space-y-3">
                    <div>
                      <h2 id="meeting-recording-heading" className="text-lg font-semibold text-foreground">Recording</h2>
                      <p className="mt-1 text-sm text-muted-foreground">Follow the transcript while you watch or listen.</p>
                    </div>
                    <StickyMediaPlayer key={mediaIdentity} meeting={meeting} />
                  </section>
                )}

                <aside
                  aria-labelledby="meeting-transcript-heading"
                  className="flex min-h-0 flex-col overflow-hidden rounded-lg border border-border bg-card lg:h-[calc(100vh-15rem)] lg:min-h-[24rem] lg:max-h-[calc(100vh-15rem)]"
                >
                  <div className="flex flex-wrap items-start justify-between gap-3 border-b border-border px-4 py-4 sm:px-5">
                    <div className="min-w-0">
                      <h2 id="meeting-transcript-heading" className="text-lg font-semibold text-foreground">Transcript</h2>
                      <p className="mt-1 text-sm text-muted-foreground" aria-live="polite">
                        {transcriptSegmentCount > 0
                          ? `${transcriptSegmentCount} ${transcriptSegmentCount === 1 ? "segment" : "segments"}`
                          : hasTranscriptText
                            ? "Text transcript without timestamped segments"
                            : "No transcript available yet"}
                      </p>
                    </div>
                    {shouldMountMediaPlayer && (
                      <span className="shrink-0 rounded-4xl border border-border bg-muted/50 px-2.5 py-1 text-xs font-medium text-muted-foreground">
                        Synced to recording
                      </span>
                    )}
                  </div>

                  <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-4 sm:px-5">
                    {meeting.segments && meeting.segments.length > 0 ? (
                      transcriptView === "roman" && meeting.transliterated_text ? (
                        (() => {
                          const rows = parseRomanRows(meeting.transliterated_text);
                          const hasSpeakers = rows.some((r) => r.speaker);
                          return (
                            <article className="space-y-5 py-4">
                              {/* Info chip — uses muted tokens per design system */}
                              <div className="flex items-start gap-2.5 rounded-lg border border-border bg-muted/50 px-3.5 py-2.5">
                                <Languages className="mt-0.5 size-4 flex-shrink-0 text-muted-foreground" aria-hidden="true" />
                                <p className="text-xs leading-relaxed text-muted-foreground">
                                  Roman transliteration — speaker attribution preserved, no timestamps.
                                </p>
                              </div>

                              {hasSpeakers ? (
                                <div className="divide-y divide-border">
                                  {rows.map((row, i) => {
                                    const speaker = row.speaker ?? "SPEAKER_UNKNOWN";
                                    const label = romanSpeakerLabel(speaker);
                                    const isUnknown = speaker === "SPEAKER_UNKNOWN";
                                    return (
                                      <div
                                        key={i}
                                        className="flex min-w-0 gap-3 border-b border-border py-4 last:border-none"
                                      >
                                        <div className="flex-shrink-0 pt-0.5">
                                          <div
                                            className={`flex size-8 items-center justify-center rounded-full text-sm font-semibold ${romanSpeakerColor(speaker)}`}
                                          >
                                            {isUnknown ? (
                                              <UserRound className="size-3.5" aria-hidden="true" />
                                            ) : (
                                              label.charAt(label.indexOf(" ") + 1)
                                            )}
                                          </div>
                                        </div>
                                        <div className="min-w-0 flex-1">
                                          <div className="mb-1 flex items-baseline gap-2">
                                            <span className="text-sm font-medium text-foreground">
                                              {label}
                                            </span>
                                          </div>
                                          <p className="break-words whitespace-pre-wrap text-sm leading-relaxed text-foreground">
                                            {row.text}
                                          </p>
                                        </div>
                                      </div>
                                    );
                                  })}
                                </div>
                              ) : (
                                <div className="space-y-4 text-sm leading-relaxed text-foreground">
                                  {rows.map((row, i) => (
                                    <p key={i} className="break-words whitespace-pre-wrap">
                                      {row.text}
                                    </p>
                                  ))}
                                </div>
                              )}
                            </article>
                          );
                        })()
                      ) : (
                        <TranscriptViewer segments={meeting.segments} isFreeTier={isFreeTier} />
                      )
                    ) : (
                      <TranscriptViewer
                        segments={meeting.segments ?? []}
                        transcriptText={meeting.transcript_text}
                        isFreeTier={isFreeTier}
                      />
                    )}
                  </div>
                </aside>
              </div>
              {isFreeTier && <PaywallModal />}
            </div>
          )}
        </div>

      </div>

      {/* Share via Email Dialog */}
      <ShareEmailDialog
        meetingId={meeting.id}
        attendees={[]}
        open={showShareDialog}
        onOpenChange={setShowShareDialog}
      />

      {/* Re-transcribe Dialog */}
      <ReTranscribeDialog
        meetingId={meeting.id}
        currentLanguage={meeting.requested_language ?? null}
        open={reTranscribeOpen}
        onClose={() => setReTranscribeOpen(false)}
        onDispatched={(newLanguage) => {
          // Optimistically flip to queued so the progress banner appears immediately
          // and clear stale transcript content. Polling picks up real state from the API.
          setMeeting((m) =>
            m
              ? {
                  ...m,
                  status: "queued",
                  sub_status: null,
                  requested_language: newLanguage ?? m.requested_language,
                  transcript_text: null,
                  transliterated_text: null,
                  segments: [],
                }
              : m
          );
          setTranscriptView("original");
          setReTranscribeOpen(false);
          startPolling();
        }}
      />
    </div>
  );
}
