// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2025-2026 Afeef Janjua
"use client";

import type { MeetingProcessingAudit, MeetingProcessingEvent, MeetingProcessingRun } from "@/app/lib/api";
import { CheckCircle2, Circle, Clock3, XCircle } from "lucide-react";

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

function stageLabel(stage: string): string {
  return stage
    .replace(/^stage_/, "")
    .replaceAll("_", " ")
    .replace(/\b\w/g, (match) => match.toUpperCase());
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
      {status}
    </span>
  );
}

function AuditEventRow({ event }: { event: MeetingProcessingEvent }) {
  const text = event.error ?? event.message;
  return (
    <li className="flex gap-3 border-b border-border py-3 last:border-b-0">
      <div className="mt-0.5 shrink-0">
        <StatusIcon status={event.status} />
      </div>
      <div className="min-w-0 flex-1 space-y-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm font-medium text-foreground">{stageLabel(event.stage)}</span>
          <StatusPill status={event.status} />
          <span className="font-mono text-xs text-muted-foreground" title={event.task_id ?? undefined}>
            task {truncateTaskId(event.task_id)}
          </span>
        </div>
        <div className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground">
          <span>start {formatDate(event.started_at ?? event.created_at)}</span>
          <span>end {formatDate(event.completed_at)}</span>
          <span>duration {formatDuration(event.started_at ?? event.created_at, event.completed_at)}</span>
        </div>
        {text && (
          <p className={`break-words text-sm ${event.error ? "text-red-700" : "text-muted-foreground"}`}>
            {text}
          </p>
        )}
      </div>
    </li>
  );
}

function AuditRun({ run }: { run: MeetingProcessingRun }) {
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
            <span>completed {formatDate(run.completed_at)}</span>
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
      {run.events.length > 0 ? (
        <ol className="mt-3 divide-y-0">
          {run.events.map((event) => <AuditEventRow key={event.id} event={event} />)}
        </ol>
      ) : (
        <p className="mt-3 text-sm text-muted-foreground">No audit events have been recorded for this run yet.</p>
      )}
    </section>
  );
}

export function MeetingProcessingAuditCard({ audit, loading = false }: MeetingProcessingAuditProps) {
  return (
    <aside className="rounded-lg border border-border bg-card p-5">
      <div className="mb-4 flex items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold text-foreground">Processing audit</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Safe audit events for this meeting&apos;s processing runs.
          </p>
        </div>
      </div>
      {loading && !audit ? (
        <p className="text-sm text-muted-foreground">Loading processing audit…</p>
      ) : !audit || audit.runs.length === 0 ? (
        <p className="text-sm text-muted-foreground">No processing audit events have been recorded yet.</p>
      ) : (
        <div className="space-y-3">
          {audit.runs.map((run) => <AuditRun key={run.id} run={run} />)}
        </div>
      )}
    </aside>
  );
}
