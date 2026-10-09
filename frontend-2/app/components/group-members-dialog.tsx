// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2025-2026 Afeef Janjua
"use client";

import { useEffect, useState } from "react";
import {
  addGroupMember,
  getApiErrorMessage,
  listGroupMembers,
  removeGroupMember,
  searchGroupUsers,
  updateGroupMember,
  type GroupMember,
  type GroupMemberRole,
  type GroupUserSearchResult,
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
import { Loader2, Search, UserPlus, X } from "lucide-react";

interface GroupMembersDialogProps {
  open: boolean;
  groupId: number;
  groupName: string;
  onOpenChange: (open: boolean) => void;
}

function getErrorMessage(error: unknown, fallback: string): string {
  return getApiErrorMessage(error) ?? (error instanceof Error && error.message ? error.message : fallback);
}

function displayName(user: { full_name: string | null; email: string }): string {
  return user.full_name?.trim() || user.email;
}

function roleLabel(role: GroupMember["role"]): string {
  return role === "owner" ? "Owner" : role === "editor" ? "Editor" : "Viewer";
}

function RoleBadge({ role }: { role: GroupMember["role"] }) {
  return (
    <span className="rounded-4xl border border-border bg-muted px-2 py-0.5 text-xs font-medium text-muted-foreground">
      {roleLabel(role)}
    </span>
  );
}

export function GroupMembersDialog({
  open,
  groupId,
  groupName,
  onOpenChange,
}: GroupMembersDialogProps) {
  const [members, setMembers] = useState<GroupMember[]>([]);
  const [query, setQuery] = useState("");
  const [role, setRole] = useState<GroupMemberRole>("viewer");
  const [results, setResults] = useState<GroupUserSearchResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [searching, setSearching] = useState(false);
  const [sharingUserId, setSharingUserId] = useState<number | null>(null);
  const [updatingUserId, setUpdatingUserId] = useState<number | null>(null);
  const [removingUserId, setRemovingUserId] = useState<number | null>(null);
  const [pendingRemoval, setPendingRemoval] = useState<GroupMember | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [statusMessage, setStatusMessage] = useState("");

  useEffect(() => {
    if (!open) return;
    let active = true;
    setLoading(true);
    setError(null);
    setStatusMessage("");
    setQuery("");
    setResults([]);
    listGroupMembers(groupId)
      .then((data) => {
        if (active) setMembers(data);
      })
      .catch((requestError) => {
        if (active) setError(getErrorMessage(requestError, "Members could not be loaded."));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [groupId, open]);

  useEffect(() => {
    if (!open) return;
    const normalizedQuery = query.trim();
    if (normalizedQuery.length < 2) {
      setResults([]);
      setSearching(false);
      return;
    }

    let active = true;
    setSearching(true);
    const timeoutId = window.setTimeout(() => {
      searchGroupUsers(groupId, normalizedQuery)
        .then((data) => {
          if (active) setResults(data);
        })
        .catch((requestError) => {
          if (active) {
            setResults([]);
            setError(getErrorMessage(requestError, "User search failed."));
          }
        })
        .finally(() => {
          if (active) setSearching(false);
        });
    }, 250);

    return () => {
      active = false;
      window.clearTimeout(timeoutId);
    };
  }, [groupId, open, query]);

  const shareUser = async (user: GroupUserSearchResult) => {
    setSharingUserId(user.user_id);
    setError(null);
    setStatusMessage("");
    try {
      const member = await addGroupMember(groupId, user.user_id, role);
      setMembers((current) => [...current, member]);
      setResults((current) => current.filter((result) => result.user_id !== user.user_id));
      setStatusMessage(`${displayName(user)} now has ${roleLabel(role).toLowerCase()} access.`);
    } catch (requestError) {
      setError(getErrorMessage(requestError, "The group could not be shared with this user."));
    } finally {
      setSharingUserId(null);
    }
  };

  const changeRole = async (member: GroupMember, nextRole: GroupMemberRole) => {
    setUpdatingUserId(member.user_id);
    setError(null);
    setStatusMessage("");
    try {
      const updated = await updateGroupMember(groupId, member.user_id, nextRole);
      setMembers((current) => current.map((item) => item.user_id === updated.user_id ? updated : item));
      setStatusMessage(`${displayName(member)} is now a ${roleLabel(nextRole).toLowerCase()}.`);
    } catch (requestError) {
      setError(getErrorMessage(requestError, "The member role could not be changed."));
    } finally {
      setUpdatingUserId(null);
    }
  };

  const confirmRemoveMember = async (member: GroupMember) => {
    setRemovingUserId(member.user_id);
    setError(null);
    setStatusMessage("");
    try {
      await removeGroupMember(groupId, member.user_id);
      setMembers((current) => current.filter((item) => item.user_id !== member.user_id));
      setPendingRemoval(null);
      setStatusMessage(`${displayName(member)} no longer has access to this group.`);
    } catch (requestError) {
      setError(getErrorMessage(requestError, "The member could not be removed."));
    } finally {
      setRemovingUserId(null);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] overflow-y-auto overscroll-contain sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>Share {groupName}</DialogTitle>
          <DialogDescription>
            Search active users by name or email, then choose whether they can view or edit this group.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-5">
          <section aria-labelledby="share-user-heading" className="space-y-3">
            <div>
              <h2 id="share-user-heading" className="text-sm font-semibold text-foreground">Add a member</h2>
              <p className="mt-1 text-xs text-muted-foreground">Search uses at least 2 characters and only returns active users.</p>
            </div>
            <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_8rem]">
              <div className="relative">
                <Search className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
                <Input
                  id="group-member-search"
                  name="group-member-search"
                  type="search"
                  value={query}
                  onChange={(event) => {
                    setQuery(event.target.value);
                    setError(null);
                  }}
                  placeholder="Name or email…"
                  autoComplete="off"
                  autoFocus
                  aria-label="Search users by name or email"
                  className="pl-8"
                  disabled={loading}
                />
              </div>
              <label className="sr-only" htmlFor="group-member-role">New member role</label>
              <select
                id="group-member-role"
                value={role}
                onChange={(event) => setRole(event.target.value as GroupMemberRole)}
                className="h-8 rounded-lg border border-input bg-background px-2 text-sm text-foreground outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
                disabled={loading}
              >
                <option value="viewer">Viewer</option>
                <option value="editor">Editor</option>
              </select>
            </div>

            {searching && (
              <p className="flex items-center gap-2 text-xs text-muted-foreground" role="status">
                <Loader2 className="size-3.5 animate-spin" aria-hidden="true" /> Searching users…
              </p>
            )}
            {!searching && query.trim().length >= 2 && results.length === 0 && (
              <p className="rounded-lg border border-dashed border-border px-3 py-4 text-sm text-muted-foreground">
                No active users match this search.
              </p>
            )}
            {results.length > 0 && (
              <div className="space-y-2" aria-label="Matching users">
                {results.map((user) => (
                  <div key={user.user_id} className="flex min-w-0 items-center justify-between gap-3 rounded-lg border border-border bg-card px-3 py-2.5">
                    <div className="min-w-0">
                      <p className="truncate text-sm font-medium text-foreground">{displayName(user)}</p>
                      <p className="truncate text-xs text-muted-foreground">{user.email}</p>
                    </div>
                    <Button
                      type="button"
                      size="sm"
                      onClick={() => void shareUser(user)}
                      loading={sharingUserId === user.user_id}
                      disabled={sharingUserId !== null}
                    >
                      <UserPlus className="size-3.5" aria-hidden="true" />
                      Share
                    </Button>
                  </div>
                ))}
              </div>
            )}
          </section>

          <section aria-labelledby="group-members-heading" className="space-y-3">
            <div className="flex items-center justify-between gap-3">
              <h2 id="group-members-heading" className="text-sm font-semibold text-foreground">Current members</h2>
              <span className="text-xs text-muted-foreground">{members.length} {members.length === 1 ? "member" : "members"}</span>
            </div>
            {loading ? (
              <div className="flex items-center gap-2 rounded-lg border border-border px-3 py-4 text-sm text-muted-foreground" role="status">
                <Loader2 className="size-4 animate-spin" aria-hidden="true" /> Loading members…
              </div>
            ) : (
              <div className="space-y-2">
                {members.map((member) => (
                  <div key={member.user_id} className="flex min-w-0 flex-col gap-3 rounded-lg border border-border bg-card px-3 py-2.5 sm:flex-row sm:items-center sm:justify-between">
                    <div className="min-w-0">
                      <p className="truncate text-sm font-medium text-foreground">{displayName(member)}</p>
                      <p className="truncate text-xs text-muted-foreground">{member.email}</p>
                    </div>
                    <div className="flex shrink-0 items-center gap-2">
                      <RoleBadge role={member.role} />
                      {member.role !== "owner" && (
                        <>
                          <label className="sr-only" htmlFor={`member-role-${member.user_id}`}>
                            Role for {displayName(member)}
                          </label>
                          <select
                            id={`member-role-${member.user_id}`}
                            value={member.role}
                            onChange={(event) => void changeRole(member, event.target.value as GroupMemberRole)}
                            className="h-7 rounded-lg border border-input bg-background px-2 text-xs text-foreground outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
                            disabled={updatingUserId !== null || removingUserId !== null}
                          >
                            <option value="viewer">Viewer</option>
                            <option value="editor">Editor</option>
                          </select>
                          <Button
                            type="button"
                            variant="ghost"
                            size="icon-sm"
                            className="text-destructive hover:text-destructive"
                            aria-label={`Remove ${displayName(member)}`}
                            onClick={() => setPendingRemoval(member)}
                            loading={removingUserId === member.user_id}
                            disabled={updatingUserId !== null || removingUserId !== null}
                          >
                            <X className="size-4" aria-hidden="true" />
                          </Button>
                        </>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </section>

          {error && <p role="alert" aria-live="assertive" className="rounded-lg border border-destructive/20 bg-destructive/10 px-3 py-2 text-sm text-destructive">{error}</p>}
          <p role="status" aria-live="polite" className="min-h-5 text-sm text-muted-foreground">{statusMessage}</p>
        </div>

        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>Close</Button>
        </DialogFooter>
      </DialogContent>
      <AlertDialog
        open={pendingRemoval !== null}
        onOpenChange={(nextOpen) => {
          if (!nextOpen && removingUserId === null) setPendingRemoval(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Remove this member?</AlertDialogTitle>
            <AlertDialogDescription>
              {pendingRemoval && `${displayName(pendingRemoval)} will lose access to ${groupName}.`}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={removingUserId !== null}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              onClick={() => pendingRemoval && void confirmRemoveMember(pendingRemoval)}
              loading={removingUserId !== null}
            >
              Remove member
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Dialog>
  );
}
