// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2025-2026 Afeef Janjua
"use client";

import Link from "next/link";
import { FolderOpen, Loader2 } from "lucide-react";
import type { GroupSummary } from "@/app/lib/api";

interface MeetingGroupSelectorProps {
  value: number | null;
  groups: GroupSummary[];
  loading?: boolean;
  saving?: boolean;
  error?: string | null;
  onChange: (groupId: number | null) => void;
}

export function MeetingGroupSelector({
  value,
  groups,
  loading = false,
  saving = false,
  error = null,
  onChange,
}: MeetingGroupSelectorProps) {
  return (
    <div className="flex min-w-0 flex-col items-start gap-1.5 sm:flex-row sm:items-center sm:gap-2">
      <label htmlFor="meeting-group" className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
        <FolderOpen className="size-3.5" aria-hidden="true" />
        Group
      </label>
      <div className="flex min-w-0 items-center gap-2">
        <select
          id="meeting-group"
          aria-label="Meeting group"
          value={value ?? ""}
          onChange={(event) => onChange(event.target.value ? Number(event.target.value) : null)}
          disabled={loading || saving}
          className="h-7 max-w-56 rounded-lg border border-border bg-background px-2 text-xs text-foreground outline-none transition-colors focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-60"
        >
          <option value="">No group</option>
          {groups.map((group) => (
            <option key={group.id} value={group.id}>
              {group.name}
            </option>
          ))}
        </select>
        {saving && <Loader2 className="size-3.5 animate-spin text-muted-foreground" aria-label="Saving group" />}
        {groups.length === 0 && !loading && (
          <Link href="/groups" className="text-xs font-medium text-primary hover:underline">
            Create one
          </Link>
        )}
      </div>
      {error && <p role="alert" className="text-xs text-destructive sm:ml-[4.75rem]">{error}</p>}
    </div>
  );
}
