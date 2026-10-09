// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2025-2026 Afeef Janjua
"use client";

import { useEffect, useState, type FormEvent } from "react";
import Link from "next/link";
import {
  createGroup,
  deleteGroup,
  getGroup,
  getGroups,
  reindexGroup,
  updateGroup,
  type GroupPayload,
  type GroupSummary,
} from "@/app/lib/api";
import { Button } from "@/app/components/ui/button";
import { Input } from "@/app/components/ui/input";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/app/components/ui/alert-dialog";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/app/components/ui/dialog";
import { ArrowRight, FolderOpen, Pencil, Plus, Trash2, Users } from "lucide-react";

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

function getErrorMessage(error: unknown, fallback: string): string {
  const detail = getErrorDetail(error);
  if (typeof detail === "string") return detail;
  if (typeof detail === "object" && detail !== null && "message" in detail) {
    const message = (detail as { message?: unknown }).message;
    if (typeof message === "string") return message;
  }
  return error instanceof Error && error.message ? error.message : fallback;
}

function formatDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "Unknown date" : new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  }).format(date);
}

interface GroupFormDialogProps {
  open: boolean;
  group: GroupSummary | null;
  onOpenChange: (open: boolean) => void;
  onSaved: (
    group: GroupSummary,
    mode: "create" | "update",
    introductionChanged: boolean,
    feedback?: string,
  ) => void;
}

function GroupFormDialog({
  open,
  group,
  onOpenChange,
  onSaved,
}: GroupFormDialogProps) {
  const isEditing = Boolean(group);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reindexPending, setReindexPending] = useState<GroupReindexPendingDetail | null>(null);
  const [reindexing, setReindexing] = useState(false);
  const [reindexFeedback, setReindexFeedback] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setName(group?.name ?? "");
    setDescription(group?.description ?? "");
    setError(null);
    setReindexPending(null);
    setReindexFeedback(null);
  }, [group, open]);

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const trimmedName = name.trim();
    if (!trimmedName) {
      setError("Group name is required.");
      return;
    }

    setSaving(true);
    setError(null);
    setReindexPending(null);
    setReindexFeedback(null);
    const payload: GroupPayload = {
      name: trimmedName,
      description: description.trim() || null,
    };

    try {
      const saved = isEditing && group
        ? await updateGroup(group.id, payload)
        : await createGroup(payload);
      onSaved(
        saved,
        isEditing ? "update" : "create",
        Boolean(isEditing && group && group.description !== payload.description),
      );
      onOpenChange(false);
    } catch (requestError) {
      const pending = getGroupReindexPending(requestError);
      if (!pending) {
        setError(getErrorMessage(requestError, "The group could not be saved."));
      } else {
        setError(pending.message);
        setReindexPending(pending);
        if (group) {
          try {
            const durableGroup = await getGroup(group.id);
            onSaved(
              durableGroup,
              "update",
              false,
              "Group saved. Its AI index is pending; retry the index in this dialog.",
            );
          } catch {
            // Keep the entered description and pending retry action if the refresh also fails.
          }
        }
      }
    } finally {
      setSaving(false);
    }
  };

  const handleRetryReindex = async () => {
    if (!reindexPending || reindexing) return;
    setReindexing(true);
    setError(null);
    setReindexFeedback(null);
    try {
      await reindexGroup(reindexPending.group_id);
      setReindexPending(null);
      setReindexFeedback("The saved group introduction is preserved. Its AI index refresh was queued.");
      onOpenChange(false);
    } catch (requestError) {
      setError(getErrorMessage(requestError, "The AI index could not be queued. Please retry."));
    } finally {
      setReindexing(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{isEditing ? "Edit group" : "Create a group"}</DialogTitle>
          <DialogDescription>
            Groups organize meetings and provide the context used by AI Chat.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="space-y-1.5">
            <label htmlFor="group-name" className="text-sm font-medium text-foreground">
              Name
            </label>
            <Input
              id="group-name"
              name="group-name"
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="e.g. Product team…"
              maxLength={100}
              autoFocus
              autoComplete="off"
              disabled={saving || reindexing}
            />
            <p className="text-xs text-muted-foreground">Up to 100 characters.</p>
          </div>
          <div className="space-y-1.5">
            <label htmlFor="group-description" className="text-sm font-medium text-foreground">
              Introduction / context for AI <span className="font-normal text-muted-foreground">(optional)</span>
            </label>
            <textarea
              id="group-description"
              name="group-description"
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              placeholder="e.g. context for planning meetings…"
              maxLength={500}
              disabled={saving || reindexing}
              rows={4}
              className="w-full resize-none rounded-lg border border-input bg-transparent px-2.5 py-2 text-sm outline-none transition-colors placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-50"
            />
            <p className="text-xs text-muted-foreground">This guides group-level AI queries. Up to 500 characters.</p>
          </div>
          {error && (
            <p role="alert" className="rounded-lg border border-destructive/20 bg-destructive/10 px-3 py-2 text-sm text-destructive">
              {error}
            </p>
          )}
          {reindexPending && (
            <div className="space-y-2 rounded-lg border border-primary/25 bg-primary/10 px-3 py-2 text-sm text-foreground">
              <p>The saved description is preserved. Retry the AI index without changing it.</p>
              <Button
                type="button"
                variant="outline"
                onClick={() => void handleRetryReindex()}
                disabled={reindexing}
                loading={reindexing}
              >
                Retry AI index
              </Button>
            </div>
          )}
          {reindexFeedback && (
            <p role="status" aria-live="polite" className="text-sm text-muted-foreground">
              {reindexFeedback}
            </p>
          )}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)} disabled={saving || reindexing}>
              Cancel
            </Button>
            <Button type="submit" loading={saving} disabled={reindexing}>
              {isEditing ? "Save changes" : "Create group"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export default function GroupsPage() {
  const [groups, setGroups] = useState<GroupSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [formOpen, setFormOpen] = useState(false);
  const [editingGroup, setEditingGroup] = useState<GroupSummary | null>(null);
  const [deletingGroup, setDeletingGroup] = useState<GroupSummary | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [saveFeedback, setSaveFeedback] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;
    getGroups()
      .then((data) => {
        if (mounted) {
          setGroups(data);
          const editParam = new URLSearchParams(window.location.search).get("edit");
          const editGroupId = editParam && /^\d+$/.test(editParam) ? Number(editParam) : null;
          const groupToEdit = editGroupId === null
            ? null
            : data.find((group) => group.id === editGroupId) ?? null;
          if (groupToEdit?.can_edit) {
            setEditingGroup(groupToEdit);
            setFormOpen(true);
          }
        }
      })
      .catch((requestError) => {
        if (mounted) setError(getErrorMessage(requestError, "Groups could not be loaded."));
      })
      .finally(() => {
        if (mounted) setLoading(false);
      });
    return () => {
      mounted = false;
    };
  }, []);

  const openCreateDialog = () => {
    setEditingGroup(null);
    setFormOpen(true);
  };

  const openEditDialog = (group: GroupSummary) => {
    setEditingGroup(group);
    setFormOpen(true);
  };

  const handleSaved = (
    saved: GroupSummary,
    mode: "create" | "update",
    introductionChanged: boolean,
    feedback?: string,
  ) => {
    setGroups((current) => mode === "create"
      ? [saved, ...current]
      : current.map((group) => (group.id === saved.id ? saved : group)));
    setError(null);
    setSaveFeedback(feedback ?? (
      introductionChanged
        ? "Group saved. The AI index refresh has been queued for the new introduction."
        : "Group saved."
    ));
  };

  const handleDelete = async () => {
    if (!deletingGroup) return;
    setDeleting(true);
    setDeleteError(null);
    try {
      await deleteGroup(deletingGroup.id);
      setGroups((current) => current.filter((group) => group.id !== deletingGroup.id));
      setDeletingGroup(null);
    } catch (requestError) {
      setDeleteError(getErrorMessage(
        requestError,
        "This group could not be deleted. Unassign its meetings first and try again.",
      ));
    } finally {
      setDeleting(false);
    }
  };

  return (
    <div className="mx-auto w-full max-w-5xl space-y-8 p-6 lg:p-8">
      <header className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <div className="mb-2 flex items-center gap-2 text-primary">
            <Users className="size-5" aria-hidden="true" />
            <span className="text-sm font-medium">Meeting context</span>
          </div>
          <h1 className="text-2xl font-semibold text-foreground">Groups</h1>
          <p className="mt-2 max-w-2xl text-sm text-muted-foreground">
            Organize meetings into shared context groups and guide AI Chat with an optional introduction.
          </p>
        </div>
        <Button onClick={openCreateDialog}>
          <Plus className="size-4" aria-hidden="true" />
          New group
        </Button>
      </header>

      {error && (
        <div role="alert" className="rounded-lg border border-destructive/20 bg-destructive/10 px-4 py-3 text-sm text-destructive">
          {error}
        </div>
      )}

      <div aria-live="polite" className="min-h-5 text-sm text-muted-foreground">{saveFeedback}</div>

      {loading ? (
        <div className="rounded-lg border border-border bg-card px-6 py-12 text-center text-sm text-muted-foreground">
          Loading groups…
        </div>
      ) : groups.length === 0 ? (
        <section className="rounded-lg border border-dashed border-border bg-card px-6 py-14 text-center">
          <FolderOpen className="mx-auto mb-4 size-8 text-muted-foreground" aria-hidden="true" />
          <h2 className="text-lg font-semibold text-foreground">Create your first group</h2>
          <p className="mx-auto mt-2 max-w-md text-sm text-muted-foreground">
            After creating a group, open a meeting and select it in the meeting header. Assigned meetings are indexed for secure group search and AI Chat.
          </p>
          <Button className="mt-6" onClick={openCreateDialog}>
            <Plus className="size-4" aria-hidden="true" />
            Create group
          </Button>
        </section>
      ) : (
        <section aria-labelledby="groups-heading" className="space-y-3">
          <div className="flex items-center justify-between">
            <h2 id="groups-heading" className="text-lg font-semibold text-foreground">Available groups</h2>
            <span className="text-sm text-muted-foreground">{groups.length} {groups.length === 1 ? "group" : "groups"}</span>
          </div>
          <div className="grid gap-3 md:grid-cols-2">
            {groups.map((group) => (
              <article key={group.id} className="rounded-lg border border-border bg-card p-5">
                <div className="flex items-start justify-between gap-4">
                  <div className="min-w-0">
                    <h3 className="truncate text-base font-semibold text-foreground">{group.name}</h3>
                    <p className="mt-1 min-h-10 break-words text-sm text-muted-foreground">
                      {group.description || "No AI introduction added yet."}
                    </p>
                  </div>
                  <Users className="mt-0.5 size-5 shrink-0 text-muted-foreground" aria-hidden="true" />
                </div>
                <div className="mt-5 flex flex-wrap items-center justify-between gap-2 border-t border-border pt-3">
                   <div className="flex flex-wrap items-center gap-2">
                     <span className="rounded-4xl border border-border bg-muted px-2 py-0.5 text-xs font-medium text-muted-foreground">
                       {group.access_role === "owner" ? "Owner" : group.access_role === "editor" ? "Editor" : "Viewer"}
                     </span>
                     <span className="text-xs text-muted-foreground">Created {formatDate(group.created_at)}</span>
                   </div>
                   <div className="flex flex-wrap items-center justify-end gap-1">
                    <Link
                      href={`/groups/${group.id}`}
                      aria-label={`Open group ${group.name}`}
                      className="inline-flex min-h-7 items-center gap-1.5 rounded-lg px-2 py-1 text-sm font-medium text-primary outline-none hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring/50"
                    >
                      Open group
                      <ArrowRight className="size-3.5" aria-hidden="true" />
                    </Link>
                     {group.can_edit && (
                       <Button variant="ghost" size="sm" onClick={() => openEditDialog(group)}>
                         <Pencil className="size-3.5" aria-hidden="true" />
                         Edit
                       </Button>
                     )}
                     {group.can_delete && (
                       <Button
                         variant="ghost"
                         size="sm"
                         className="text-destructive hover:text-destructive"
                         onClick={() => {
                           setDeleteError(null);
                           setDeletingGroup(group);
                         }}
                       >
                         <Trash2 className="size-3.5" aria-hidden="true" />
                         Delete
                       </Button>
                     )}
                  </div>
                </div>
              </article>
            ))}
          </div>
        </section>
      )}

      <GroupFormDialog
        key={`${formOpen ? "open" : "closed"}-${editingGroup?.id ?? "new"}`}
        open={formOpen}
        group={editingGroup}
        onOpenChange={setFormOpen}
        onSaved={handleSaved}
      />

      <AlertDialog
        open={Boolean(deletingGroup)}
        onOpenChange={(open) => {
          if (!open && !deleting) {
            setDeletingGroup(null);
            setDeleteError(null);
          }
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete {deletingGroup?.name}?</AlertDialogTitle>
            <AlertDialogDescription>
              This removes the group. Meetings assigned to it may need to be unassigned before the group can be deleted.
            </AlertDialogDescription>
          </AlertDialogHeader>
          {deleteError && (
            <p role="alert" className="rounded-lg border border-destructive/20 bg-destructive/10 px-3 py-2 text-sm text-destructive">
              {deleteError}
            </p>
          )}
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleting}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              onClick={handleDelete}
              loading={deleting}
            >
              Delete group
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
