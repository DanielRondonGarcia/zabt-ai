// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2025-2026 Afeef Janjua
"use client";

import { useEffect, useState, type FormEvent } from "react";
import {
  createGroup,
  deleteGroup,
  getGroups,
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
import { FolderOpen, Pencil, Plus, Trash2, Users } from "lucide-react";

function getErrorMessage(error: unknown, fallback: string): string {
  if (typeof error === "object" && error !== null && "response" in error) {
    const response = (error as { response?: { data?: { detail?: unknown } } }).response;
    if (typeof response?.data?.detail === "string") return response.data.detail;
  }
  return error instanceof Error && error.message ? error.message : fallback;
}

function formatDate(value: string): string {
  return new Date(value).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

interface GroupFormDialogProps {
  open: boolean;
  group: GroupSummary | null;
  onOpenChange: (open: boolean) => void;
  onSaved: (group: GroupSummary, mode: "create" | "update") => void;
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

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const trimmedName = name.trim();
    if (!trimmedName) {
      setError("Group name is required.");
      return;
    }

    setSaving(true);
    setError(null);
    const payload: GroupPayload = {
      name: trimmedName,
      description: description.trim() || null,
    };

    try {
      const saved = isEditing && group
        ? await updateGroup(group.id, payload)
        : await createGroup(payload);
      onSaved(saved, isEditing ? "update" : "create");
      onOpenChange(false);
    } catch (requestError) {
      setError(getErrorMessage(requestError, "The group could not be saved."));
    } finally {
      setSaving(false);
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
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="e.g. Product team"
              maxLength={100}
              autoFocus
              disabled={saving}
            />
            <p className="text-xs text-muted-foreground">Up to 100 characters.</p>
          </div>
          <div className="space-y-1.5">
            <label htmlFor="group-description" className="text-sm font-medium text-foreground">
              Description <span className="font-normal text-muted-foreground">(optional)</span>
            </label>
            <textarea
              id="group-description"
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              placeholder="What meetings belong here?"
              maxLength={500}
              disabled={saving}
              rows={4}
              className="w-full resize-none rounded-lg border border-input bg-transparent px-2.5 py-2 text-sm outline-none transition-colors placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-50"
            />
            <p className="text-xs text-muted-foreground">Up to 500 characters.</p>
          </div>
          {error && (
            <p role="alert" className="rounded-lg border border-destructive/20 bg-destructive/10 px-3 py-2 text-sm text-destructive">
              {error}
            </p>
          )}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)} disabled={saving}>
              Cancel
            </Button>
            <Button type="submit" loading={saving}>
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

  useEffect(() => {
    let mounted = true;
    getGroups()
      .then((data) => {
        if (mounted) setGroups(data);
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

  const handleSaved = (saved: GroupSummary, mode: "create" | "update") => {
    setGroups((current) => mode === "create"
      ? [saved, ...current]
      : current.map((group) => (group.id === saved.id ? saved : group)));
    setError(null);
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
            Organize meetings into owner-only groups so retrieval and AI Chat can use the right context.
          </p>
        </div>
        <Button onClick={openCreateDialog}>
          <Plus className="size-4" />
          New group
        </Button>
      </header>

      {error && (
        <div role="alert" className="rounded-lg border border-destructive/20 bg-destructive/10 px-4 py-3 text-sm text-destructive">
          {error}
        </div>
      )}

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
            <Plus className="size-4" />
            Create group
          </Button>
        </section>
      ) : (
        <section aria-labelledby="groups-heading" className="space-y-3">
          <div className="flex items-center justify-between">
            <h2 id="groups-heading" className="text-lg font-semibold text-foreground">Your groups</h2>
            <span className="text-sm text-muted-foreground">{groups.length} {groups.length === 1 ? "group" : "groups"}</span>
          </div>
          <div className="grid gap-3 md:grid-cols-2">
            {groups.map((group) => (
              <article key={group.id} className="rounded-lg border border-border bg-card p-5">
                <div className="flex items-start justify-between gap-4">
                  <div className="min-w-0">
                    <h3 className="truncate text-base font-semibold text-foreground">{group.name}</h3>
                    <p className="mt-1 min-h-10 text-sm text-muted-foreground">
                      {group.description || "No description added yet."}
                    </p>
                  </div>
                  <Users className="mt-0.5 size-5 shrink-0 text-muted-foreground" aria-hidden="true" />
                </div>
                <div className="mt-5 flex items-center justify-between border-t border-border pt-3">
                  <span className="text-xs text-muted-foreground">Created {formatDate(group.created_at)}</span>
                  <div className="flex items-center gap-1">
                    <Button variant="ghost" size="sm" onClick={() => openEditDialog(group)}>
                      <Pencil className="size-3.5" />
                      Edit
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="text-destructive hover:text-destructive"
                      onClick={() => {
                        setDeleteError(null);
                        setDeletingGroup(group);
                      }}
                    >
                      <Trash2 className="size-3.5" />
                      Delete
                    </Button>
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
