// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2025-2026 Afeef Janjua
"use client";

import { use, useEffect, useMemo, useState, type FormEvent } from "react";
import Link from "next/link";
import {
  ArrowLeft,
  ArrowRight,
  CalendarDays,
  Clock3,
  FileAudio,
  FolderOpen,
  Loader2,
  Pencil,
  Search,
  Share2,
  Video,
} from "lucide-react";
import {
  assignMeetingGroup,
  getApiErrorStatus,
  getGroup,
  getGroups,
  getMeetings,
  reindexGroup,
  updateGroup,
  type GroupSummary,
  type Meeting,
} from "@/app/lib/api";
import { StatusBadge } from "@/app/components/status-badge";
import { Button } from "@/app/components/ui/button";
import { Input } from "@/app/components/ui/input";
import { GroupMembersDialog } from "@/app/components/group-members-dialog";
import { ChatMarkdown } from "@/app/components/chat-markdown";

const dateFormatter = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  year: "numeric",
});
const numberFormatter = new Intl.NumberFormat("en-US");

function parseGroupId(value: string): number | null {
  if (!/^\d+$/.test(value)) return null;
  const parsed = Number(value);
  return Number.isSafeInteger(parsed) && parsed > 0 ? parsed : null;
}

function formatDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "Unknown date" : dateFormatter.format(date);
}

function formatCount(value: number): string {
  return numberFormatter.format(value);
}

function formatDuration(seconds: number | null): string {
  if (seconds === null || seconds === undefined) return "Duration unavailable";
  if (seconds < 60) return `${numberFormatter.format(Math.round(seconds))} sec`;
  return `${numberFormatter.format(Math.round(seconds / 60))} min`;
}

function formatSubStatus(value: string): string {
  const formatted = value.replace(/_/g, " ").trim();
  return formatted ? formatted.charAt(0).toUpperCase() + formatted.slice(1) : value;
}

function formatSource(meeting: Meeting): string {
  if (meeting.source_type === "youtube") {
    return meeting.youtube_channel ? `YouTube · ${meeting.youtube_channel}` : "YouTube";
  }
  if (meeting.source_type === "record") return "Recorded audio";
  return meeting.media_type === "video" ? "Uploaded video" : "Uploaded audio";
}

function getErrorMessage(error: unknown, fallback: string): string {
  if (typeof error === "object" && error !== null && "response" in error) {
    const response = (error as { response?: { data?: { detail?: unknown } } }).response;
    if (typeof response?.data?.detail === "string") return response.data.detail;
    if (typeof response?.data?.detail === "object" && response.data.detail !== null && "message" in response.data.detail) {
      const message = (response.data.detail as { message?: unknown }).message;
      if (typeof message === "string") return message;
    }
  }
  return error instanceof Error && error.message ? error.message : fallback;
}

interface GroupReindexPendingDetail {
  code: "group_reindex_pending";
  group_id: number;
  message: string;
}

function getErrorDetail(error: unknown): unknown {
  if (typeof error !== "object" || error === null || !("response" in error)) return undefined;
  return (error as { response?: { data?: { detail?: unknown } } }).response?.data?.detail;
}

function getGroupReindexPending(error: unknown): GroupReindexPendingDetail | null {
  const detail = getErrorDetail(error);
  if (typeof detail !== "object" || detail === null) return null;
  const value = detail as Partial<GroupReindexPendingDetail>;
  if (value.code !== "group_reindex_pending" || typeof value.group_id !== "number" || typeof value.message !== "string") {
    return null;
  }
  return {
    code: value.code,
    group_id: value.group_id,
    message: value.message,
  };
}

const linkActionClass =
  "inline-flex min-h-8 items-center justify-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium outline-none hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring/50";

interface AssignmentError {
  meetingId: number;
  targetGroupId: number | null;
  message: string;
}

interface MeetingRowProps {
  meeting: Meeting;
  currentGroupId: number;
  groupNames: Map<number, string>;
  actionLabel: "Add to group" | "Move here" | "Remove from group";
  actionTarget: number | null;
  saving: boolean;
  assignmentError: AssignmentError | null;
  onAssign: (meeting: Meeting, targetGroupId: number | null) => void;
  onRetry: () => void;
  canManage: boolean;
}

function MeetingRow({
  meeting,
  currentGroupId,
  groupNames,
  actionLabel,
  actionTarget,
  saving,
  assignmentError,
  onAssign,
  onRetry,
  canManage,
}: MeetingRowProps) {
  const currentGroupName = meeting.group_id !== null && meeting.group_id !== currentGroupId
    ? groupNames.get(meeting.group_id) ?? "another group"
    : null;
  const visibleSubStatus = meeting.sub_status && meeting.status !== "processing"
    ? formatSubStatus(meeting.sub_status)
    : null;
  const rowError = assignmentError?.meetingId === meeting.id ? assignmentError : null;
  const isRemoveAction = actionTarget === null;

  return (
    <article className="min-w-0 rounded-lg border border-border bg-background p-4">
      <div className="min-w-0 space-y-2">
        <div className="flex min-w-0 flex-col gap-2 sm:flex-row sm:items-start">
          <h3 className="min-w-0 flex-1 break-words line-clamp-2 text-base font-semibold text-foreground">
            {meeting.title}
          </h3>
          <div className="flex shrink-0 flex-wrap items-center gap-2">
            <StatusBadge status={meeting.status} subStatus={meeting.sub_status} />
            {visibleSubStatus && (
              <span className="max-w-full break-words rounded-4xl border border-border px-2 py-0.5 text-xs text-muted-foreground">
                {visibleSubStatus}
              </span>
            )}
          </div>
        </div>

        <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
          <span className="inline-flex min-w-0 items-center gap-1.5">
            <CalendarDays className="size-3.5 shrink-0" aria-hidden="true" />
            <span>{formatDate(meeting.created_at)}</span>
          </span>
          <span className="inline-flex min-w-0 items-center gap-1.5">
            <Clock3 className="size-3.5 shrink-0" aria-hidden="true" />
            <span>{formatDuration(meeting.duration_seconds)}</span>
          </span>
          <span className="inline-flex min-w-0 items-center gap-1.5 break-words">
            {meeting.source_type === "youtube" ? (
              <Video className="size-3.5 shrink-0" aria-hidden="true" />
            ) : (
              <FileAudio className="size-3.5 shrink-0" aria-hidden="true" />
            )}
            <span className="break-words">{formatSource(meeting)}</span>
          </span>
        </div>

        {currentGroupName && (
          <p className="break-words text-xs text-muted-foreground">
            Currently in <span className="font-medium text-foreground">{currentGroupName}</span>
          </p>
        )}

        {rowError && (
          <div role="alert" className="flex flex-wrap items-center gap-2 rounded-lg border border-destructive/20 bg-destructive/10 px-3 py-2 text-xs text-destructive">
            <span className="min-w-0 flex-1 break-words">{rowError.message}</span>
            <button
              type="button"
              onClick={onRetry}
              disabled={saving}
              className="rounded-lg px-2 py-1 font-medium underline underline-offset-2 outline-none hover:bg-destructive/10 focus-visible:ring-2 focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-60"
            >
              Try again
            </button>
          </div>
        )}
      </div>

      <div className="mt-4 flex flex-wrap items-center justify-end gap-2 border-t border-border pt-3">
        <Link href={`/meetings/${meeting.id}`} className={`${linkActionClass} text-primary hover:text-primary`}>
          Open meeting
          <ArrowRight className="size-3.5" aria-hidden="true" />
        </Link>
        {canManage && (
          <Button
            type="button"
            size="sm"
            variant={isRemoveAction ? "outline" : "default"}
            className={isRemoveAction ? "text-destructive hover:text-destructive" : undefined}
            onClick={() => onAssign(meeting, actionTarget)}
            disabled={saving || meeting.group_id === actionTarget}
            loading={saving}
          >
            {actionLabel}
          </Button>
        )}
      </div>
    </article>
  );
}

export default function GroupDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const groupId = parseGroupId(id);
  const [group, setGroup] = useState<GroupSummary | null>(null);
  const [accessibleGroups, setAccessibleGroups] = useState<GroupSummary[]>([]);
  const [groupMeetings, setGroupMeetings] = useState<Meeting[]>([]);
  const [ownedMeetings, setOwnedMeetings] = useState<Meeting[]>([]);
  const [loading, setLoading] = useState(groupId !== null);
  const [notFound, setNotFound] = useState(groupId === null);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [searchTerm, setSearchTerm] = useState("");
  const [savingMeetingIds, setSavingMeetingIds] = useState<Set<number>>(() => new Set());
  const [assignmentError, setAssignmentError] = useState<AssignmentError | null>(null);
  const [saveFeedback, setSaveFeedback] = useState<string | null>(null);
  const [editingIntroduction, setEditingIntroduction] = useState(false);
  const [introductionDraft, setIntroductionDraft] = useState("");
  const [savingIntroduction, setSavingIntroduction] = useState(false);
  const [introductionError, setIntroductionError] = useState<string | null>(null);
  const [reindexPending, setReindexPending] = useState<GroupReindexPendingDetail | null>(null);
  const [reindexing, setReindexing] = useState(false);
  const [membersDialogOpen, setMembersDialogOpen] = useState(false);

  useEffect(() => {
    let mounted = true;

    if (groupId === null) {
      return () => {
        mounted = false;
      };
    }

    const load = async () => {
      await Promise.resolve();
      if (!mounted) return;

      setLoading(true);
      setNotFound(false);
      setError(null);
      setGroup(null);
      setAccessibleGroups([]);
      setGroupMeetings([]);
      setOwnedMeetings([]);
      setSearchTerm("");
      setSavingMeetingIds(new Set());
      setAssignmentError(null);
      setSaveFeedback(null);
      setEditingIntroduction(false);
      setIntroductionDraft("");
      setIntroductionError(null);
      setReindexPending(null);
      setReindexing(false);

      try {
        const [selectedGroup, selectedGroupMeetings, ownedMeetingData, groupData] = await Promise.all([
          getGroup(groupId),
          getMeetings(0, 100, groupId),
          getMeetings(0, 100),
          getGroups(),
        ]);
        if (!mounted) return;
        setGroup(selectedGroup);
        setIntroductionDraft(selectedGroup.description ?? "");
        setGroupMeetings(selectedGroupMeetings);
        setOwnedMeetings(ownedMeetingData);
        setAccessibleGroups(groupData);
      } catch (requestError) {
        if (!mounted) return;
        if (getApiErrorStatus(requestError) === 404) {
          setNotFound(true);
          return;
        }
        setError(getErrorMessage(
          requestError,
          "This group could not be loaded. Check your connection, then try again.",
        ));
      } finally {
        if (mounted) setLoading(false);
      }
    };

    void load();

    return () => {
      mounted = false;
    };
  }, [groupId, reloadKey]);

  const groupNames = useMemo(() => {
    const names = new Map(accessibleGroups.map((accessibleGroup) => [accessibleGroup.id, accessibleGroup.name]));
    if (group) names.set(group.id, group.name);
    return names;
  }, [accessibleGroups, group]);

  const assignedMeetings = useMemo(
    () => groupMeetings.filter((meeting) => meeting.group_id === groupId),
    [groupId, groupMeetings],
  );
  const availableMeetings = useMemo(
    () => ownedMeetings.filter((meeting) => meeting.group_id !== groupId),
    [groupId, ownedMeetings],
  );
  const filteredAvailableMeetings = useMemo(() => {
    const normalizedSearch = searchTerm.trim().toLowerCase();
    if (!normalizedSearch) return availableMeetings;

    return availableMeetings.filter((meeting) => {
      const currentGroupName = meeting.group_id === null
        ? ""
        : groupNames.get(meeting.group_id) ?? "another group";
      const searchableText = [
        meeting.title,
        meeting.description ?? "",
        formatSource(meeting),
        currentGroupName,
      ].join(" ").toLowerCase();
      return searchableText.includes(normalizedSearch);
    });
  }, [availableMeetings, groupNames, searchTerm]);

  const handleAssignment = async (meeting: Meeting, targetGroupId: number | null) => {
    if (!group || savingMeetingIds.has(meeting.id) || meeting.group_id === targetGroupId) return;

    setSavingMeetingIds((current) => new Set(current).add(meeting.id));
    setAssignmentError(null);
    setSaveFeedback(null);

    try {
      const updatedMeeting = await assignMeetingGroup(meeting.id, targetGroupId);
      setGroupMeetings((currentMeetings) => {
        if (targetGroupId === group.id) {
          const alreadyListed = currentMeetings.some((item) => item.id === updatedMeeting.id);
          return alreadyListed
            ? currentMeetings.map((item) => item.id === updatedMeeting.id ? updatedMeeting : item)
            : [updatedMeeting, ...currentMeetings];
        }
        return currentMeetings.filter((item) => item.id !== updatedMeeting.id);
      });
      setOwnedMeetings((currentMeetings) => currentMeetings.map((item) => (
        item.id === updatedMeeting.id ? updatedMeeting : item
      )));
      setSaveFeedback(targetGroupId === null
        ? `${meeting.title} was removed from ${group.name}.`
        : meeting.group_id === null
          ? `${meeting.title} was added to ${group.name}.`
          : `${meeting.title} was moved to ${group.name}.`);
    } catch (requestError) {
      setAssignmentError({
        meetingId: meeting.id,
        targetGroupId,
        message: `${getErrorMessage(requestError, "The meeting could not be updated.")} Check your connection and try again using this row action.`,
      });
    } finally {
      setSavingMeetingIds((current) => {
        const next = new Set(current);
        next.delete(meeting.id);
        return next;
      });
    }
  };

  const retryAssignment = () => {
    if (!assignmentError) return;
    const meeting = [...groupMeetings, ...ownedMeetings].find((currentMeeting) => currentMeeting.id === assignmentError.meetingId);
    if (meeting) void handleAssignment(meeting, assignmentError.targetGroupId);
  };

  const openIntroductionEditor = () => {
    if (!group || !group.can_edit) return;
    setIntroductionDraft(group.description ?? "");
    setIntroductionError(null);
    setReindexPending(null);
    setSaveFeedback(null);
    setEditingIntroduction(true);
  };

  const cancelIntroductionEdit = () => {
    if (!group || savingIntroduction || reindexing) return;
    setIntroductionDraft(group.description ?? "");
    setIntroductionError(null);
    setReindexPending(null);
    setEditingIntroduction(false);
  };

  const handleSaveIntroduction = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!group || !group.can_edit || savingIntroduction || reindexing) return;

    const description = introductionDraft.trim() || null;
    const introductionChanged = group.description !== description;
    setSavingIntroduction(true);
    setIntroductionError(null);
    setReindexPending(null);
    setSaveFeedback(null);

    try {
      const saved = await updateGroup(group.id, { description });
      setGroup(saved);
      setAccessibleGroups((currentGroups) => currentGroups.map((currentGroup) => (
        currentGroup.id === saved.id ? saved : currentGroup
      )));
      setIntroductionDraft(saved.description ?? "");
      setEditingIntroduction(false);
      setSaveFeedback(introductionChanged
        ? "Group saved. The AI index refresh has been queued for the new human guidance."
        : "Group saved.");
    } catch (requestError) {
      const pending = getGroupReindexPending(requestError);
      if (!pending) {
        setIntroductionError(getErrorMessage(requestError, "The group introduction could not be saved."));
      } else {
        setIntroductionError(pending.message);
        setReindexPending(pending);
        try {
          const durableGroup = await getGroup(group.id);
          setGroup(durableGroup);
          setAccessibleGroups((currentGroups) => currentGroups.map((currentGroup) => (
            currentGroup.id === durableGroup.id ? durableGroup : currentGroup
          )));
          setIntroductionDraft(durableGroup.description ?? "");
          setSaveFeedback("Group saved. Its AI index is pending; retry the AI index below.");
        } catch {
          // Keep the entered notes and pending retry action if the refresh also fails.
        }
      }
    } finally {
      setSavingIntroduction(false);
    }
  };

  const handleRetryIntroductionReindex = async () => {
    if (!reindexPending || reindexing) return;
    setReindexing(true);
    setIntroductionError(null);
    try {
      await reindexGroup(reindexPending.group_id);
      setReindexPending(null);
      setEditingIntroduction(false);
      setSaveFeedback("The saved group introduction is preserved. Its AI index refresh was queued.");
    } catch (requestError) {
      setIntroductionError(getErrorMessage(requestError, "The AI index could not be queued. Please retry."));
    } finally {
      setReindexing(false);
    }
  };

  if (loading) {
    return (
      <div className="mx-auto flex w-full max-w-6xl items-center justify-center p-6 lg:p-8">
        <div className="flex flex-col items-center gap-3 rounded-lg border border-border bg-card px-8 py-12 text-center">
          <Loader2 className="size-6 animate-spin text-muted-foreground" aria-hidden="true" />
          <p className="text-sm font-medium text-muted-foreground">Loading group…</p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="mx-auto w-full max-w-6xl space-y-6 overflow-x-hidden p-4 sm:p-6 lg:p-8">
        <Link href="/groups" className={`${linkActionClass} w-fit text-primary hover:text-primary`}>
          <ArrowLeft className="size-4" aria-hidden="true" />
          Back to groups
        </Link>
        <section role="alert" className="rounded-lg border border-destructive/20 bg-destructive/10 px-6 py-10 text-center">
          <h1 className="text-lg font-semibold text-destructive">Unable to load this group</h1>
          <p className="mx-auto mt-2 max-w-lg break-words text-sm text-destructive">{error}</p>
          <Button type="button" className="mt-6" onClick={() => setReloadKey((current) => current + 1)}>
            Try again
          </Button>
        </section>
      </div>
    );
  }

  if (groupId === null || notFound || !group) {
    return (
      <div className="mx-auto w-full max-w-6xl space-y-6 overflow-x-hidden p-4 sm:p-6 lg:p-8">
        <Link href="/groups" className={`${linkActionClass} w-fit text-primary hover:text-primary`}>
          <ArrowLeft className="size-4" aria-hidden="true" />
          Back to groups
        </Link>
        <section className="rounded-lg border border-dashed border-border bg-card px-6 py-14 text-center">
          <FolderOpen className="mx-auto mb-4 size-8 text-muted-foreground" aria-hidden="true" />
          <h1 className="text-2xl font-semibold text-foreground">Group not found</h1>
          <p className="mx-auto mt-2 max-w-md break-words text-sm text-muted-foreground">
            This group may have been deleted, or you may not have access to it. Return to Groups to choose another one.
          </p>
          <Link href="/groups" className={`${linkActionClass} mt-6 bg-primary text-primary-foreground hover:bg-primary/90`}>
            View groups
          </Link>
        </section>
      </div>
    );
  }

  return (
    <div className="mx-auto w-full max-w-6xl space-y-6 overflow-x-hidden p-4 sm:p-6 lg:p-8">
      <header className="space-y-5">
        <Link href="/groups" className={`${linkActionClass} -ml-3 w-fit text-muted-foreground hover:text-foreground`}>
          <ArrowLeft className="size-4" aria-hidden="true" />
          Back to groups
        </Link>

        <div className="flex min-w-0 flex-col gap-5 lg:flex-row lg:items-start lg:justify-between">
          <div className="min-w-0 space-y-4">
            <div className="flex min-w-0 items-start gap-3">
              <div className="flex size-12 shrink-0 items-center justify-center rounded-lg border border-border bg-muted text-primary">
                <FolderOpen className="size-6" aria-hidden="true" />
              </div>
              <div className="min-w-0">
                <h1 className="break-words text-2xl font-semibold text-foreground">{group.name}</h1>
                <span className="mt-2 inline-flex rounded-4xl border border-border bg-muted px-2.5 py-1 text-xs font-medium text-muted-foreground">
                  {group.access_role === "owner" ? "Owner" : group.access_role === "editor" ? "Editor" : "Viewer"}
                </span>
              </div>
            </div>

            <dl className="grid max-w-2xl grid-cols-2 gap-3 sm:grid-cols-3">
              <div className="rounded-lg border border-border bg-card px-3 py-2.5">
                <dt className="text-xs font-medium text-muted-foreground">Created</dt>
                <dd className="mt-1 break-words text-sm font-medium text-foreground">{formatDate(group.created_at)}</dd>
              </div>
              <div className="rounded-lg border border-border bg-card px-3 py-2.5">
                <dt className="text-xs font-medium text-muted-foreground">Assigned</dt>
                <dd className="mt-1 tabular-nums text-sm font-semibold text-foreground">{formatCount(assignedMeetings.length)}</dd>
              </div>
              <div className="rounded-lg border border-border bg-card px-3 py-2.5">
                <dt className="text-xs font-medium text-muted-foreground">Available</dt>
                <dd className="mt-1 tabular-nums text-sm font-semibold text-foreground">{formatCount(availableMeetings.length)}</dd>
              </div>
            </dl>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            {group.can_manage_members && (
              <Button type="button" variant="outline" onClick={() => setMembersDialogOpen(true)}>
                <Share2 className="size-3.5" aria-hidden="true" />
                Share
              </Button>
            )}
          </div>
        </div>
      </header>

      <div aria-live="polite" className="min-h-5 text-sm text-muted-foreground">
        {saveFeedback}
      </div>

      <section aria-labelledby="group-introduction-heading" className="min-w-0 rounded-lg border border-border bg-card p-4 sm:p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 id="group-introduction-heading" className="text-lg font-semibold text-foreground">Group introduction</h2>
            <p className="mt-1 max-w-3xl break-words text-sm text-muted-foreground">
              Human notes and general guidelines for this group knowledge base, available to people and AI Chat.
            </p>
          </div>
          {group.can_edit && !editingIntroduction && (
            <Button type="button" variant="outline" onClick={openIntroductionEditor}>
              <Pencil className="size-3.5" aria-hidden="true" />
              Edit introduction
            </Button>
          )}
        </div>

        {editingIntroduction ? (
          <form onSubmit={handleSaveIntroduction} className="mt-5 space-y-4">
            <div className="space-y-1.5">
              <label htmlFor="group-introduction" className="text-sm font-medium text-foreground">
                Human notes &amp; general guidelines <span className="font-normal text-muted-foreground">(optional)</span>
              </label>
              <textarea
                id="group-introduction"
                name="group-introduction"
                value={introductionDraft}
                onChange={(event) => setIntroductionDraft(event.target.value)}
                placeholder="e.g. Keep planning decisions tied to an owner and a date…"
                maxLength={500}
                rows={6}
                autoComplete="off"
                aria-describedby="group-introduction-help"
                disabled={savingIntroduction || reindexing}
                className="w-full resize-y rounded-lg border border-input bg-transparent px-2.5 py-2 text-sm outline-none transition-colors placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-50"
              />
              <p id="group-introduction-help" className="break-words text-xs text-muted-foreground">
                Supports Markdown. These notes guide the group knowledge base and AI Chat. Up to 500 characters.
              </p>
            </div>

            {introductionError && (
              <p role="alert" aria-live="assertive" className="break-words rounded-lg border border-destructive/20 bg-destructive/10 px-3 py-2 text-sm text-destructive">
                {introductionError}
              </p>
            )}
            {reindexPending && (
              <div className="space-y-2 rounded-lg border border-primary/25 bg-primary/10 px-3 py-2 text-sm text-foreground">
                <p className="break-words">The saved introduction is preserved. Retry the AI index without changing it.</p>
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => void handleRetryIntroductionReindex()}
                  disabled={reindexing}
                  loading={reindexing}
                >
                  Retry AI index
                </Button>
              </div>
            )}

            <div className="flex flex-wrap justify-end gap-2">
              <Button type="button" variant="outline" onClick={cancelIntroductionEdit} disabled={savingIntroduction || reindexing}>
                Cancel
              </Button>
              <Button type="submit" loading={savingIntroduction} disabled={reindexing}>
                Save introduction
              </Button>
            </div>
          </form>
        ) : group.description ? (
          <div className="mt-5 max-h-[28rem] min-w-0 overflow-y-auto overscroll-contain rounded-lg border border-border bg-muted/30 p-4 sm:p-5">
            {/* Omit citation data so human notes cannot turn model citation tokens into meeting links. */}
            <ChatMarkdown content={group.description} />
          </div>
        ) : (
          <p className="mt-5 rounded-lg border border-dashed border-border bg-muted/30 px-4 py-6 break-words text-sm text-muted-foreground">
            No human notes or general guidelines have been added yet.
            {group.can_edit ? " Add guidance to give this group a clear knowledge-base context." : ""}
          </p>
        )}
      </section>

      <div className="grid min-w-0 gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.15fr)]">
        <section aria-labelledby="assigned-meetings-heading" className="min-w-0 rounded-lg border border-border bg-card p-4 sm:p-5">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              <h2 id="assigned-meetings-heading" className="text-lg font-semibold text-foreground">Assigned meetings</h2>
              <p className="mt-1 break-words text-sm text-muted-foreground">
                Meetings in this group are available to its retrieval and AI Chat context.
              </p>
            </div>
            <span className="shrink-0 rounded-4xl bg-muted px-2.5 py-1 text-xs font-medium tabular-nums text-muted-foreground">
              {formatCount(assignedMeetings.length)} assigned
            </span>
          </div>

          {assignedMeetings.length === 0 ? (
            <div className="mt-5 rounded-lg border border-dashed border-border px-5 py-10 text-center">
              <FolderOpen className="mx-auto mb-3 size-7 text-muted-foreground" aria-hidden="true" />
              <h3 className="text-base font-semibold text-foreground">No meetings in this group yet</h3>
              <p className="mx-auto mt-2 max-w-md break-words text-sm text-muted-foreground">
                Add an available meeting or move one from another group using the panel beside this list.
              </p>
            </div>
          ) : (
            <div className="mt-5 max-h-[38rem] min-w-0 space-y-3 overflow-x-hidden overflow-y-auto overscroll-contain pr-1">
              {assignedMeetings.map((meeting) => (
                <MeetingRow
                  key={meeting.id}
                  meeting={meeting}
                  currentGroupId={group.id}
                  groupNames={groupNames}
                  actionLabel="Remove from group"
                  actionTarget={null}
                  saving={savingMeetingIds.has(meeting.id)}
                  assignmentError={assignmentError}
                  onAssign={handleAssignment}
                  onRetry={retryAssignment}
                  canManage={group.can_edit}
                />
              ))}
            </div>
          )}
        </section>

        <section aria-labelledby="add-meetings-heading" className="min-w-0 rounded-lg border border-border bg-muted/30 p-4 sm:p-5">
          <div className="min-w-0">
            <h2 id="add-meetings-heading" className="text-lg font-semibold text-foreground">{group.can_edit ? "Manage meetings" : "View-only access"}</h2>
            <p className="mt-1 break-words text-sm text-muted-foreground">
              {group.can_edit
                ? "Add your meetings or move one from another group."
                : "You can read meetings in this group and ask AI Chat questions, but you cannot change group content."}
            </p>
          </div>

          {group.can_edit && <div className="mt-5 space-y-1.5">
            <label htmlFor="meeting-search" className="text-sm font-medium text-foreground">
              Search available meetings
            </label>
            <div className="relative">
              <Search className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
              <Input
                id="meeting-search"
                name="meeting-search"
                type="search"
                autoComplete="off"
                value={searchTerm}
                onChange={(event) => setSearchTerm(event.target.value)}
                placeholder="Search meetings…"
                className="pl-8"
              />
            </div>
          </div>}

          {group.can_edit && (availableMeetings.length === 0 ? (
            <div className="mt-5 rounded-lg border border-dashed border-border bg-card px-4 py-8 text-center">
              <p className="break-words text-sm text-muted-foreground">
                There are no available meetings. Create or upload one from the Meetings page.
              </p>
              <Link href="/meetings" className={`${linkActionClass} mt-4 bg-primary text-primary-foreground hover:bg-primary/90`}>
                Go to Meetings
                <ArrowRight className="size-3.5" aria-hidden="true" />
              </Link>
            </div>
          ) : filteredAvailableMeetings.length === 0 ? (
            <div className="mt-5 rounded-lg border border-dashed border-border bg-card px-4 py-8 text-center">
              <p className="break-words text-sm text-muted-foreground">
                No available meetings match “{searchTerm}”. Clear the search to see all available meetings.
              </p>
            </div>
          ) : (
            <div className="mt-5 max-h-[38rem] min-w-0 space-y-3 overflow-x-hidden overflow-y-auto overscroll-contain pr-1">
              {filteredAvailableMeetings.map((meeting) => (
                <MeetingRow
                  key={meeting.id}
                  meeting={meeting}
                  currentGroupId={group.id}
                  groupNames={groupNames}
                  actionLabel={meeting.group_id === null ? "Add to group" : "Move here"}
                  actionTarget={group.id}
                  saving={savingMeetingIds.has(meeting.id)}
                  assignmentError={assignmentError}
                  onAssign={handleAssignment}
                  onRetry={retryAssignment}
                  canManage={group.can_edit}
                />
              ))}
            </div>
          ))}
        </section>
      </div>
      <GroupMembersDialog
        open={membersDialogOpen}
        groupId={group.id}
        groupName={group.name}
        onOpenChange={setMembersDialogOpen}
      />
    </div>
  );
}
