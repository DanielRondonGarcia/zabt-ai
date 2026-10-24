// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2025-2026 Afeef Janjua
"use client";

import { useState } from "react";
import type { MeetingProcessingAudit, MeetingProcessingEvent, MeetingProcessingRun } from "@/app/lib/api";
import { CheckCircle2, Circle, Clock3, XCircle } from "lucide-react";
import { formatSummaryProgress, formatSummaryCorrelationId } from "@/app/lib/stage-utils";

interface MeetingProcessingAuditProps {
  audit: MeetingProcessingAudit | null;
  loading?: boolean;
}

const statusClasses: Record<string, string> = {
  queued: "border-stone-200 bg-stone-50 text-stone-600",
  running: "border-amber-200 bg-amber-50 text-amber-700",
  completed: "border-emerald-200 bg-emerald-50 text-emerald-700",
  failed: "border-red-200 bg-red-50 text-red-700",
  started: "border-amber-200 bg-amber-50 text-amber-700",
};

function formatDate(value: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatDuration(start: string | null, end: string | null): string {
  if (!start || !end) return "—";
  const startMs = new Date(start).getTime();
  const endMs = new Date(end).getTime();
  if (Number.isNaN(startMs) || Number.isNaN(endMs) || endMs < startMs) return "—";
  const seconds = Math.round((endMs - startMs) / 1000);
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return rest ? `${minutes}m ${rest}s` : `${minutes}m`;
}

function truncateTaskId(taskId: string | null): string {
  if (!taskId) return "—";
  if (taskId.length <= 18) return taskId;
  return `${taskId.slice(0, 8)}…${taskId.slice(-6)}`;
}

const stageLabels: Record<string, string> = {
  stage_download: "Prepare audio",
  stage_youtube_download: "Fetch YouTube audio",
  stage_transcribe: "Transcribe audio",
  stage_transliterate: "Transliterate audio",
  stage_summarize: "Summarize",
  stage_summarize_progress: "Summary progress",
  stage_extract_intelligence: "Extract intelligence",
  stage_infer_title: "Infer title",
  stage_extract_highlights: "Extract highlights",
  stage_extract_structured_output: "Extract structured output",
  stage_embedding: "Generate embeddings",
  stage_visual_breakdown: "Visual breakdown",
  stage_optional_visual_breakdown: "Visual breakdown",
};

const stageDescriptions: Record<string, string> = {
  stage_summarize: "Generates and saves the meeting summary.",
  stage_summarize_progress: "Per-window and final-synthesis audit; summary content is not stored.",
  stage_infer_title: "Suggests a title from the saved summary, keeping custom titles.",
  stage_extract_highlights: "Extracts chapters, action items, and key questions from the transcript.",
  stage_extract_structured_output: "Extracts structured details for the meeting type.",
  stage_download:
    "Checks audio in storage and retrieves it for transcription when needed. Nothing is downloaded to your device.",
  stage_youtube_download:
    "Fetches the audio track from the linked YouTube video before transcription.",
};

function stageLabel(stage: string): string {
  return (
    stageLabels[stage] ??
    stage
      .replace(/^stage_/, "")
      .replaceAll("_", " ")
      .replace(/\b\w/g, (match) => match.toUpperCase())
  );
}

function statusDisplay(status: string): string {
  if (status === "started") return "in progress";
  return status;
}

// Backend writes bare machine markers such as `stage_download started` /
// `stage_download completed`. They add no information once the start and
// terminal events of the same stage are merged into one row, so hide them.
function isRawStageMarker(event: MeetingProcessingEvent): boolean {
  if (!event.message) return false;
  return event.message === `${event.stage} started` || event.message === `${event.stage} completed`;
}

interface StageGroup {
  key: string;
  start: MeetingProcessingEvent | null;
  terminal: MeetingProcessingEvent | null;
  extras: MeetingProcessingEvent[];
}

// Group each stage's started event with its terminal event (completed /
// failed / skipped / cancelled) for the same task into one chronological row.
// Retries recorded under a different task id, unpaired events, and errors
// still get their own rows.
function buildStageGroups(events: MeetingProcessingEvent[]): StageGroup[] {
  const groups: StageGroup[] = [];
  const open = new Map<string, StageGroup>();
  for (const event of events) {
    const key = `${event.stage}::${event.task_id ?? ""}`;
    const current = open.get(key);
    if (event.status === "started") {
      if (current && !current.terminal) {
        current.extras.push(event);
      } else {
        const group: StageGroup = { key: `${key}::${event.id}`, start: event, terminal: null, extras: [] };
        groups.push(group);
        open.set(key, group);
      }
    } else if (current && !current.terminal) {
      current.terminal = event;
      open.delete(key);
    } else {
      groups.push({ key: `${key}::${event.id}`, start: null, terminal: event, extras: [] });
    }
  }
  return groups;
}

// A stage group's outcome: its terminal status when paired, otherwise the
// stage is still running.
function groupOutcome(group: StageGroup): string {
  return group.terminal?.status ?? "running";
}

function StatusIcon({ status }: { status: string }) {
  if (status === "completed") return <CheckCircle2 className="size-4 text-emerald-600" aria-hidden="true" />;
  if (status === "failed") return <XCircle className="size-4 text-red-600" aria-hidden="true" />;
  if (status === "started" || status === "running") return <Clock3 className="size-4 text-amber-600" aria-hidden="true" />;
  return <Circle className="size-4 text-stone-400" aria-hidden="true" />;
}

function StatusPill({ status }: { status: string }) {
  return (
    <span className={`rounded-4xl border px-2.5 py-1 text-xs font-medium ${statusClasses[status] ?? statusClasses.queued}`}>
      {statusDisplay(status)}
    </span>
  );
}

function StageRow({ group }: { group: StageGroup }) {
  const outcome = groupOutcome(group);
  const stage = group.start?.stage ?? group.terminal?.stage ?? "";
  const description = stageDescriptions[stage];
  const progress = stage === "stage_summarize_progress"
    ? formatSummaryProgress(group.terminal?.metadata ?? group.start?.metadata)
    : null;
  const correlationId = stage === "stage_summarize_progress"
    ? formatSummaryCorrelationId(group.terminal?.metadata ?? group.start?.metadata)
    : null;
  const startTime = group.start?.started_at ?? group.start?.created_at ?? null;
  const endTime = group.terminal?.completed_at ?? group.terminal?.created_at ?? null;

  // Keep real messages and errors; drop the bare machine markers.
  const notes: Array<{ text: string; isError: boolean }> = [];
  const seen = new Set<string>();
  const pushNote = (event: MeetingProcessingEvent | null) => {
    if (!event) return;
    const candidates: Array<{ text: string; isError: boolean }> = [];
    if (event.error) candidates.push({ text: event.error, isError: true });
    if (event.message && !isRawStageMarker(event)) candidates.push({ text: event.message, isError: false });
    for (const candidate of candidates) {
      const dedupeKey = `${candidate.isError}:${candidate.text}`;
      if (seen.has(dedupeKey)) continue;
      seen.add(dedupeKey);
      notes.push(candidate);
    }
  };
  pushNote(group.terminal);
  if (group.start) pushNote(group.start);
  for (const extra of group.extras) pushNote(extra);

  return (
    <li className="flex gap-3 border-b border-border py-3 last:border-b-0">
      <div className="mt-0.5 shrink-0">
        <StatusIcon status={outcome} />
      </div>
      <div className="min-w-0 flex-1 space-y-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm font-medium text-foreground">{stageLabel(stage)}</span>
          <StatusPill status={outcome} />
          <span className="font-mono text-xs text-muted-foreground" title={group.start?.task_id ?? group.terminal?.task_id ?? undefined}>
            task {truncateTaskId(group.start?.task_id ?? group.terminal?.task_id ?? null)}
          </span>
        </div>
        {description && <p className="text-xs text-muted-foreground">{description}</p>}
        {progress && <p className="text-xs font-medium text-muted-foreground">{progress}</p>}
        {correlationId && (
          <p
            className="font-mono text-xs text-muted-foreground"
            title="Provider correlation ID for this summary call; use it to look the request up in LiteLLM logs"
          >
            trace {correlationId}
          </p>
        )}
        <div className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground">
          <span>started {formatDate(startTime)}</span>
          {group.terminal ? (
            <span>finished {formatDate(endTime)}</span>
          ) : (
            <span>in progress</span>
          )}
          <span>duration {formatDuration(startTime, endTime)}</span>
        </div>
        {notes.map((note, index) => (
          <p
            key={index}
            className={`break-words text-sm ${note.isError ? "text-red-700" : "text-muted-foreground"}`}
          >
            {note.text}
          </p>
        ))}
      </div>
    </li>
  );
}

function AuditRun({ run }: { run: MeetingProcessingRun }) {
  const groups = buildStageGroups(run.events);
  return (
    <section className="rounded-lg border border-border bg-background p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-sm font-semibold text-foreground">{run.trigger} run</h3>
            <StatusPill status={run.status} />
          </div>
          <div className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground">
            <span>created {formatDate(run.created_at)}</span>
            <span>started {formatDate(run.started_at)}</span>
            <span>finished {formatDate(run.completed_at)}</span>
            <span>duration {formatDuration(run.started_at, run.completed_at)}</span>
          </div>
          <p className="font-mono text-xs text-muted-foreground" title={run.root_task_id ?? undefined}>
            root task {truncateTaskId(run.root_task_id)}
          </p>
        </div>
      </div>
      {run.final_error && (
        <p className="mt-3 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
          {run.final_error}
        </p>
      )}
      {groups.length > 0 ? (
        <ol className="mt-3 divide-y-0">
          {groups.map((group) => <StageRow key={group.key} group={group} />)}
        </ol>
      ) : (
        <p className="mt-3 text-sm text-muted-foreground">No stage activity has been recorded for this run yet.</p>
      )}
    </section>
  );
}

function AuditSummary({ runCount, latestStatus }: { runCount: number; latestStatus: string | null }) {
  return (
    <summary className="cursor-pointer rounded-lg px-1 py-1 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40 focus-visible:ring-offset-2 focus-visible:ring-offset-card">
      <span className="flex flex-wrap items-start justify-between gap-3">
        <span className="min-w-0 flex-1">
          <span role="heading" aria-level={2} className="block text-lg font-semibold text-foreground">
            Processing audit
          </span>
          <span className="mt-1 block text-sm text-muted-foreground">
            Stage-by-stage record of how each processing run for this meeting progressed.
          </span>
        </span>
        <span className="flex shrink-0 flex-wrap items-center justify-end gap-2">
          <span className="rounded-4xl border border-border bg-muted/50 px-2.5 py-1 text-xs font-medium text-muted-foreground">
            {runCount} {runCount === 1 ? "run" : "runs"}
          </span>
          {latestStatus && (
            <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
              Latest
              <StatusPill status={latestStatus} />
            </span>
          )}
        </span>
      </span>
    </summary>
  );
}

export function MeetingProcessingAuditCard({ audit, loading = false }: MeetingProcessingAuditProps) {
  const [isAuditOpen, setIsAuditOpen] = useState(false);
  const runs = audit?.runs ?? [];
  const latestRun = runs[0] ?? null;

  return (
    <aside className="rounded-lg border border-border bg-card p-5">
      {runs.length === 0 ? (
        <>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              <h2 className="text-lg font-semibold text-foreground">Processing audit</h2>
              <p className="mt-1 text-sm text-muted-foreground">
                Stage-by-stage record of how each processing run for this meeting progressed.
              </p>
            </div>
            <span className="shrink-0 rounded-4xl border border-border bg-muted/50 px-2.5 py-1 text-xs font-medium text-muted-foreground">
              0 runs
            </span>
          </div>
          {loading && !audit ? (
            <p className="mt-4 text-sm text-muted-foreground">Loading processing audit…</p>
          ) : (
            <p className="mt-4 text-sm text-muted-foreground">No processing runs have been recorded yet.</p>
          )}
        </>
      ) : (
        <details
          open={isAuditOpen}
          onToggle={(event) => setIsAuditOpen(event.currentTarget.open)}
        >
          <AuditSummary runCount={runs.length} latestStatus={latestRun?.status ?? null} />
          <div className="mt-4 max-h-[28rem] overflow-y-auto overscroll-contain pr-2">
            <div className="space-y-3">
              {runs.map((run) => <AuditRun key={run.id} run={run} />)}
            </div>
          </div>
        </details>
      )}
    </aside>
  );
}
