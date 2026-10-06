// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2025-2026 Afeef Janjua
"use client";

import Link from "next/link";
import { useMemo } from "react";
import { ChevronDown, ExternalLink } from "lucide-react";

import {
  formatKindCounts,
  groupCitationsByMeeting,
  type NumberedCitation,
} from "@/app/lib/ai-chat-citations";

const FOCUS_RING =
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:ring-offset-1";

interface AiChatSourcesProps {
  citations: Map<string, NumberedCitation>;
}

/** Compact, deduplicated sources row grouped by meeting with an on-demand snippet list. */
export function AiChatSources({ citations }: AiChatSourcesProps) {
  const groups = useMemo(() => groupCitationsByMeeting(citations), [citations]);

  if (groups.length === 0) {
    return (
      <div>
        <p className="text-xs font-medium uppercase tracking-wide text-stone-400">Sources</p>
        <p className="mt-1 text-sm text-stone-500">No cited snippets were returned.</p>
      </div>
    );
  }

  const snippets = groups.flatMap((group) =>
    group.citations.filter((citation) => citation.text !== null),
  );

  return (
    <div>
      <div className="flex flex-wrap items-center gap-2">
        <p className="text-xs font-medium uppercase tracking-wide text-stone-400">Sources</p>
        <ul className="flex flex-wrap items-center gap-2" aria-label="Cited meetings">
          {groups.map((group) => (
            <li key={group.meeting_id}>
              <Link
                href={`/meetings/${group.meeting_id}`}
                className={`inline-flex items-center gap-1.5 rounded-4xl border border-stone-200 bg-white px-2.5 py-1 text-xs text-stone-700 transition-colors hover:border-primary/30 hover:bg-primary/5 ${FOCUS_RING}`}
              >
                <span className="font-medium text-stone-900">Meeting {group.meeting_id}</span>
                <span aria-hidden="true" className="text-stone-300">
                  |
                </span>
                <span className="text-stone-500">{formatKindCounts(group.kindCounts)}</span>
                <ExternalLink aria-hidden="true" className="size-3 text-primary" />
              </Link>
            </li>
          ))}
        </ul>
      </div>

      {snippets.length > 0 && (
        <details className="group/snippets mt-2">
          <summary
            className={`inline-flex cursor-pointer list-none items-center gap-1 rounded-lg px-1 py-0.5 text-xs font-medium text-stone-600 hover:text-stone-900 [&::-webkit-details-marker]:hidden ${FOCUS_RING}`}
          >
            <ChevronDown
              aria-hidden="true"
              className="size-3.5 transition-transform group-open/snippets:rotate-180"
            />
            <span className="group-open/snippets:hidden">Show snippets ({snippets.length})</span>
            <span className="hidden group-open/snippets:inline">Hide snippets</span>
          </summary>
          <div className="mt-2 space-y-3">
            {groups.map((group) => {
              const groupSnippets = group.citations.filter((citation) => citation.text !== null);
              if (groupSnippets.length === 0) return null;
              return (
                <section
                  key={group.meeting_id}
                  aria-label={`Snippets from meeting ${group.meeting_id}`}
                  className="rounded-lg border border-stone-200 bg-white"
                >
                  <div className="flex items-center justify-between gap-3 border-b border-stone-200 px-3 py-2 text-xs font-medium text-stone-500">
                    <span>Meeting {group.meeting_id}</span>
                    <Link
                      href={`/meetings/${group.meeting_id}`}
                      className={`inline-flex items-center gap-1 rounded-md text-primary hover:underline ${FOCUS_RING}`}
                    >
                      Open meeting
                      <ExternalLink aria-hidden="true" className="size-3" />
                    </Link>
                  </div>
                  <ol className="divide-y divide-stone-100">
                    {groupSnippets.map((citation) => (
                      <li key={`${citation.kind}-${citation.chunk_index}`} className="flex gap-3 px-3 py-2">
                        <span className="mt-0.5 inline-flex h-5 min-w-5 shrink-0 items-center justify-center rounded-4xl border border-primary/30 bg-primary/10 px-1.5 text-xs font-medium leading-none text-primary">
                          <span className="sr-only">Source </span>
                          {citation.number}
                        </span>
                        <div className="min-w-0 flex-1">
                          <p className="text-xs font-medium text-stone-500">
                            {citation.kind} · chunk {citation.chunk_index + 1}
                          </p>
                          <p className="mt-0.5 break-words text-sm text-stone-700">{citation.text}</p>
                        </div>
                      </li>
                    ))}
                  </ol>
                </section>
              );
            })}
          </div>
        </details>
      )}
    </div>
  );
}
