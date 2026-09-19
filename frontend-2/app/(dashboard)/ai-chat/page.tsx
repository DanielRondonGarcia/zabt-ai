// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2025-2026 Afeef Janjua
"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertCircle,
  ArrowRight,
  Bot,
  ExternalLink,
  Loader2,
  MessageSquare,
  Search,
  Users,
} from "lucide-react";

import { askAiChat, getGroups, type AIChatSource, type GroupSummary } from "@/app/lib/api";

type ChatTurn = {
  id: number;
  groupId: number;
  question: string;
  answer: string;
  sources: AIChatSource[];
  error?: string;
};

const sourceLabel = (source: AIChatSource) =>
  `${source.kind || "source"} · chunk ${source.chunk_index + 1}`;

export default function AIChatPage() {
  const searchParams = useSearchParams();
  const initialQuery = searchParams.get("q")?.trim() ?? "";
  const initialQuerySubmitted = useRef(false);

  const [groups, setGroups] = useState<GroupSummary[]>([]);
  const [selectedGroupId, setSelectedGroupId] = useState<number | null>(null);
  const [message, setMessage] = useState(initialQuery);
  const [history, setHistory] = useState<ChatTurn[]>([]);
  const [groupsLoading, setGroupsLoading] = useState(true);
  const [groupsError, setGroupsError] = useState<string | null>(null);
  const [isAsking, setIsAsking] = useState(false);
  const [chatError, setChatError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;

    getGroups()
      .then((loadedGroups) => {
        if (!active) return;
        setGroups(loadedGroups);
        setSelectedGroupId((current) => current ?? loadedGroups[0]?.id ?? null);
        setGroupsError(null);
      })
      .catch(() => {
        if (!active) return;
        setGroupsError("We could not load your groups. Please refresh and try again.");
      })
      .finally(() => {
        if (active) setGroupsLoading(false);
      });

    return () => {
      active = false;
    };
  }, []);

  const selectedGroup = useMemo(
    () => groups.find((group) => group.id === selectedGroupId) ?? null,
    [groups, selectedGroupId]
  );

  const submitQuestion = async (question: string) => {
    const trimmed = question.trim();
    if (!trimmed || selectedGroupId === null || isAsking) return;

    setIsAsking(true);
    setChatError(null);

    try {
      const response = await askAiChat({ groupId: selectedGroupId, message: trimmed });
      setHistory((current) => [
        ...current,
        {
          id: Date.now(),
          groupId: selectedGroupId,
          question: trimmed,
          answer: response.answer.trim(),
          sources: response.sources,
        },
      ]);
      setMessage("");
    } catch {
      const error = "Zabt could not answer that question right now. Please try again.";
      setChatError(error);
      setHistory((current) => [
        ...current,
        {
          id: Date.now(),
          groupId: selectedGroupId,
          question: trimmed,
          answer: "",
          sources: [],
          error,
        },
      ]);
    } finally {
      setIsAsking(false);
    }
  };

  useEffect(() => {
    if (initialQuerySubmitted.current || !initialQuery || selectedGroupId === null || groupsLoading) return;
    initialQuerySubmitted.current = true;
    void submitQuestion(initialQuery);
  }, [groupsLoading, initialQuery, selectedGroupId]);

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    void submitQuestion(message);
  };

  const canSubmit = Boolean(message.trim()) && selectedGroupId !== null && !groupsLoading && !isAsking;
  const noGroups = !groupsLoading && !groupsError && groups.length === 0;

  return (
    <main className="px-8 py-8">
      <div className="mb-8 flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <div className="mb-3 inline-flex items-center gap-2 rounded-lg border border-primary/20 bg-primary/10 px-3 py-1 text-xs font-medium text-primary">
            <Bot aria-hidden="true" className="h-3.5 w-3.5" />
            Group-aware answers
          </div>
          <h1 className="text-2xl font-bold text-stone-900">AI Chat</h1>
          <p className="mt-1 max-w-2xl text-sm text-stone-500">
            Ask questions across one selected group. Answers are grounded in retrieved meeting snippets.
          </p>
        </div>
      </div>

      <div className="grid gap-6 lg:grid-cols-[280px_1fr]">
        <section className="rounded-lg border border-stone-200 bg-white p-4" aria-labelledby="group-selector-heading">
          <div className="mb-4 flex items-center gap-2">
            <Users aria-hidden="true" className="h-4 w-4 text-primary" />
            <h2 id="group-selector-heading" className="text-lg font-semibold text-stone-900">
              Select a group
            </h2>
          </div>

          {groupsLoading && (
            <div className="flex items-center gap-2 rounded-lg border border-stone-200 bg-stone-50 p-3 text-sm text-stone-500">
              <Loader2 aria-hidden="true" className="h-4 w-4 animate-spin text-primary" />
              Loading groups…
            </div>
          )}

          {groupsError && (
            <div role="alert" className="rounded-lg border border-primary/25 bg-primary/10 p-3 text-sm text-stone-700">
              <div className="flex items-start gap-2">
                <AlertCircle aria-hidden="true" className="mt-0.5 h-4 w-4 flex-shrink-0 text-primary" />
                <p>{groupsError}</p>
              </div>
            </div>
          )}

          {noGroups && (
            <div className="rounded-lg border border-stone-200 bg-stone-50 p-3 text-sm text-stone-600">
              No groups are available yet. Add meetings to a group before asking group-level questions.
            </div>
          )}

          {!groupsLoading && groups.length > 0 && (
            <fieldset className="space-y-2" disabled={isAsking}>
              <legend className="sr-only">Choose exactly one group for AI chat</legend>
              {groups.map((group) => (
                <label
                  key={group.id}
                  className={`block cursor-pointer rounded-lg border p-3 transition-colors ${
                    selectedGroupId === group.id
                      ? "border-primary/40 bg-primary/10 text-stone-900"
                      : "border-stone-200 bg-white text-stone-700 hover:bg-stone-50"
                  } ${isAsking ? "cursor-not-allowed opacity-70" : ""}`}
                >
                  <span className="flex items-start gap-3">
                    <input
                      type="radio"
                      name="group"
                      value={group.id}
                      checked={selectedGroupId === group.id}
                      onChange={() => setSelectedGroupId(group.id)}
                      className="mt-1 h-4 w-4 accent-primary"
                    />
                    <span className="min-w-0">
                      <span className="block text-sm font-medium">{group.name}</span>
                      {group.description && (
                        <span className="mt-1 block text-xs text-stone-500">{group.description}</span>
                      )}
                    </span>
                  </span>
                </label>
              ))}
            </fieldset>
          )}
        </section>

        <section className="rounded-lg border border-stone-200 bg-white" aria-labelledby="chat-heading">
          <div className="border-b border-stone-200 p-5">
            <div className="flex items-center gap-2">
              <MessageSquare aria-hidden="true" className="h-4 w-4 text-primary" />
              <h2 id="chat-heading" className="text-lg font-semibold text-stone-900">
                Ask Zabt
              </h2>
            </div>
            <p className="mt-1 text-sm text-stone-500">
              {selectedGroup ? `Current group: ${selectedGroup.name}` : "Select a group to start chatting."}
            </p>
          </div>

          <div className="min-h-[360px] space-y-4 p-5" aria-live="polite">
            {history.length === 0 && !isAsking && (
              <div className="flex min-h-[260px] flex-col items-center justify-center rounded-lg border border-dashed border-stone-200 bg-stone-50 p-6 text-center">
                <Search aria-hidden="true" className="mb-3 h-6 w-6 text-primary" />
                <p className="text-sm font-medium text-stone-800">Ask a question about a selected group.</p>
                <p className="mt-1 max-w-md text-sm text-stone-500">
                  Try asking for decisions, risks, open questions, or themes across meetings in that group.
                </p>
              </div>
            )}

            {history.map((turn) => (
              <article key={turn.id} className="space-y-3 rounded-lg border border-stone-200 bg-stone-50 p-4">
                <div>
                  <p className="text-xs font-medium uppercase tracking-wide text-stone-400">You asked</p>
                  <p className="mt-1 text-sm text-stone-800">{turn.question}</p>
                </div>

                {turn.error ? (
                  <div role="alert" className="rounded-lg border border-primary/25 bg-primary/10 p-3 text-sm text-stone-700">
                    <div className="flex items-start gap-2">
                      <AlertCircle aria-hidden="true" className="mt-0.5 h-4 w-4 flex-shrink-0 text-primary" />
                      <p>{turn.error}</p>
                    </div>
                  </div>
                ) : (
                  <div>
                    <p className="text-xs font-medium uppercase tracking-wide text-stone-400">Zabt answered</p>
                    <p className="mt-1 whitespace-pre-wrap text-sm leading-6 text-stone-800">
                      {turn.answer || "Zabt did not return an answer for that question."}
                    </p>
                  </div>
                )}

                {!turn.error && (
                  <div>
                    <p className="text-xs font-medium uppercase tracking-wide text-stone-400">Sources</p>
                    {turn.sources.length === 0 ? (
                      <p className="mt-1 text-sm text-stone-500">No cited snippets were returned.</p>
                    ) : (
                      <ul className="mt-2 space-y-2">
                        {turn.sources.map((source) => (
                          <li key={`${turn.id}-${source.meeting_id}-${source.kind}-${source.chunk_index}`}>
                            <Link
                              href={`/meetings/${source.meeting_id}`}
                              className="block rounded-lg border border-stone-200 bg-white p-3 text-sm transition-colors hover:border-primary/30 hover:bg-primary/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/30"
                            >
                              <span className="mb-1 flex items-center justify-between gap-3 text-xs font-medium text-stone-500">
                                <span>{sourceLabel(source)}</span>
                                <span className="inline-flex items-center gap-1 text-primary">
                                  Meeting {source.meeting_id}
                                  <ExternalLink aria-hidden="true" className="h-3 w-3" />
                                </span>
                              </span>
                              <span className="block text-stone-700">{source.text}</span>
                            </Link>
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                )}
              </article>
            ))}

            {isAsking && (
              <div className="flex items-center gap-2 rounded-lg border border-stone-200 bg-stone-50 p-4 text-sm text-stone-600">
                <Loader2 aria-hidden="true" className="h-4 w-4 animate-spin text-primary" />
                Asking Zabt…
              </div>
            )}
          </div>

          <form onSubmit={handleSubmit} className="border-t border-stone-200 p-5">
            {chatError && (
              <p role="alert" className="mb-3 text-sm text-primary">
                {chatError}
              </p>
            )}
            <label htmlFor="ai-chat-message" className="sr-only">
              Ask a question about the selected group
            </label>
            <div className="flex flex-col gap-3 sm:flex-row">
              <textarea
                id="ai-chat-message"
                value={message}
                onChange={(event) => setMessage(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    void submitQuestion(message);
                  }
                }}
                rows={2}
                placeholder="Ask about decisions, blockers, or themes…"
                disabled={noGroups || Boolean(groupsError)}
                className="min-h-12 flex-1 resize-y rounded-lg border border-stone-200 bg-white px-3 py-2 text-sm text-stone-800 placeholder-stone-400 transition-colors focus:border-primary/40 focus:outline-none focus:ring-2 focus:ring-primary/20 disabled:cursor-not-allowed disabled:bg-stone-100 disabled:text-stone-400"
              />
              <button
                type="submit"
                disabled={!canSubmit}
                className="inline-flex items-center justify-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/30 disabled:cursor-not-allowed disabled:bg-stone-200 disabled:text-stone-400"
              >
                {isAsking ? (
                  <Loader2 aria-hidden="true" className="h-4 w-4 animate-spin" />
                ) : (
                  <ArrowRight aria-hidden="true" className="h-4 w-4" />
                )}
                Ask
              </button>
            </div>
            <p className="mt-2 text-xs text-stone-400">Press Enter to submit, or Shift+Enter for a new line.</p>
          </form>
        </section>
      </div>
    </main>
  );
}
