// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2025-2026 Afeef Janjua
"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertCircle,
  ArrowRight,
  Bot,
  Loader2,
  MessageSquare,
  Plus,
  RefreshCw,
  Search,
  Trash2,
  Users,
} from "lucide-react";

import { AiChatSources } from "@/app/components/ai-chat-sources";
import { ChatMarkdown } from "@/app/components/chat-markdown";
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
import { Button } from "@/app/components/ui/button";
import { buildCitationIndex } from "@/app/lib/ai-chat-citations";
import {
  askAiChat,
  deleteAiChatConversation,
  getAiChatConversation,
  getApiErrorStatus,
  getGroups,
  listAiChatConversations,
  reindexGroup,
  type AIChatConversationSummary,
  type AIChatEvidenceStatus,
  type AIChatMessage,
  type AIChatSource,
  type GroupSummary,
} from "@/app/lib/api";
import { cn } from "@/app/lib/utils";

type ReindexState = "idle" | "loading" | "success" | "error";
type LoadStatus = "idle" | "loading" | "error";

type ThreadMessage = {
  key: string;
  role: "user" | "assistant";
  groupId: number;
  content: string;
  sources: AIChatSource[];
  evidence_status: AIChatEvidenceStatus | null;
  error?: string;
  reindexState: ReindexState;
  reindexFeedback: string | null;
};

const FOCUS_RING =
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:ring-offset-1";

const GENERIC_CHAT_ERROR = "Zabt could not answer that question right now. Please try again.";
const UNAVAILABLE_CHAT_ERROR =
  "Zabt is temporarily unavailable. Please wait a moment and try again.";

const updatedAtFormatter = new Intl.DateTimeFormat(undefined, {
  month: "short",
  day: "numeric",
  hour: "numeric",
  minute: "2-digit",
});

let localMessageCounter = 0;
const nextLocalKey = (prefix: string) => `${prefix}-${Date.now()}-${localMessageCounter++}`;

const fromApiMessage = (message: AIChatMessage, groupId: number): ThreadMessage => ({
  key: `api-${message.id}`,
  role: message.role,
  groupId,
  content: message.content,
  sources: message.sources,
  evidence_status: message.evidence_status,
  reindexState: "idle",
  reindexFeedback: null,
});

interface AssistantMessageProps {
  message: ThreadMessage;
  onReindex: (messageKey: string, groupId: number) => void;
}

function AssistantMessage({ message, onReindex }: AssistantMessageProps) {
  const citations = useMemo(
    () => buildCitationIndex(message.sources, message.content),
    [message.sources, message.content],
  );

  return (
    <article
      aria-label="Zabt answer"
      className="space-y-3 rounded-lg border border-stone-200 bg-stone-50 p-4"
    >
      <p className="flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-stone-400">
        <Bot aria-hidden="true" className="size-3.5 text-primary" />
        Zabt
      </p>

      {message.error ? (
        <div role="alert" className="rounded-lg border border-primary/25 bg-primary/10 p-3 text-sm text-stone-700">
          <div className="flex items-start gap-2">
            <AlertCircle aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-primary" />
            <p>{message.error}</p>
          </div>
        </div>
      ) : (
        <ChatMarkdown
          content={message.content || "Zabt did not return an answer for that question."}
          citations={citations}
        />
      )}

      {message.evidence_status === "insufficient" && (
        <div className="rounded-lg border border-dashed border-primary/30 bg-primary/5 p-4">
          <p className="text-sm font-medium text-stone-800">No matching meeting evidence was found.</p>
          <p className="mt-1 text-sm text-stone-600">
            The selected group index may need refreshing. Queue a refresh, then ask your question again.
          </p>
          <Button
            type="button"
            variant="outline"
            onClick={() => onReindex(message.key, message.groupId)}
            disabled={message.reindexState === "loading" || message.reindexState === "success"}
            className="mt-3 border-primary/30 hover:bg-primary/5"
          >
            {message.reindexState === "loading" ? (
              <Loader2 aria-hidden="true" className="animate-spin motion-reduce:animate-none text-primary" />
            ) : (
              <RefreshCw aria-hidden="true" className="text-primary" />
            )}
            {message.reindexState === "loading"
              ? "Refreshing index…"
              : message.reindexState === "success"
                ? "Index refresh queued"
                : "Refresh group index"}
          </Button>
          {message.reindexFeedback && (
            <p
              role={message.reindexState === "error" ? "alert" : "status"}
              className={cn("mt-2 text-sm", message.reindexState === "error" ? "text-primary" : "text-stone-600")}
            >
              {message.reindexFeedback}
            </p>
          )}
        </div>
      )}

      {/* Sources only make sense for evidence-backed answers; `null` (legacy rows) behaves like `not_required`. */}
      {!message.error && message.evidence_status === "available" && (
        <AiChatSources citations={citations} />
      )}
    </article>
  );
}

function UserMessage({ message }: { message: ThreadMessage }) {
  return (
    <article
      aria-label="Your question"
      className="ml-auto w-fit max-w-[85%] rounded-lg border border-primary/20 bg-primary/10 px-4 py-3"
    >
      <p className="text-xs font-medium uppercase tracking-wide text-stone-400">You</p>
      <p className="mt-1 whitespace-pre-wrap break-words text-sm text-stone-800">{message.content}</p>
    </article>
  );
}

export default function AIChatPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  // Captured once: the query is removed from the URL after submission, and that change must
  // not re-run the bootstrap effect.
  const [initialQuery] = useState(() => searchParams.get("q")?.trim() ?? "");

  const [groups, setGroups] = useState<GroupSummary[]>([]);
  const [selectedGroupId, setSelectedGroupId] = useState<number | null>(null);
  const [groupsLoading, setGroupsLoading] = useState(true);
  const [groupsError, setGroupsError] = useState<string | null>(null);

  const [conversations, setConversations] = useState<AIChatConversationSummary[]>([]);
  const [conversationsStatus, setConversationsStatus] = useState<LoadStatus>("idle");
  const [activeConversationId, setActiveConversationId] = useState<number | null>(null);
  const [pendingDelete, setPendingDelete] = useState<AIChatConversationSummary | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const [messages, setMessages] = useState<ThreadMessage[]>([]);
  const [threadStatus, setThreadStatus] = useState<LoadStatus>("idle");
  const [message, setMessage] = useState(initialQuery);
  const [isAsking, setIsAsking] = useState(false);
  const [chatError, setChatError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [announcement, setAnnouncement] = useState("");

  const conversationsRequestRef = useRef<number | null>(null);
  const threadRequestRef = useRef<number | null>(null);
  const askingRef = useRef(false);
  const pendingScrollRef = useRef<ScrollBehavior | null>(null);
  const messageListRef = useRef<HTMLDivElement>(null);
  const composerRef = useRef<HTMLTextAreaElement>(null);

  const loadConversations = useCallback(async (groupId: number, options?: { silent?: boolean }) => {
    conversationsRequestRef.current = groupId;
    if (!options?.silent) setConversationsStatus("loading");
    try {
      const items = await listAiChatConversations(groupId);
      if (conversationsRequestRef.current !== groupId) return;
      setConversations(items);
      setConversationsStatus("idle");
    } catch {
      if (conversationsRequestRef.current !== groupId) return;
      setConversationsStatus("error");
    }
  }, []);

  const submitQuestion = useCallback(
    async (question: string, groupId: number, conversationId: number | null) => {
      const trimmed = question.trim();
      if (!trimmed || askingRef.current) return;

      askingRef.current = true;
      setIsAsking(true);
      setChatError(null);
      setAnnouncement("Asking Zabt…");
      pendingScrollRef.current = null;

      const userMessage: ThreadMessage = {
        key: nextLocalKey("user"),
        role: "user",
        groupId,
        content: trimmed,
        sources: [],
        evidence_status: null,
        reindexState: "idle",
        reindexFeedback: null,
      };
      setMessages((current) => [...current, userMessage]);
      setMessage("");

      try {
        let response;
        let startedFreshConversation: string | null = null;
        try {
          response = await askAiChat({
            groupId,
            message: trimmed,
            conversationId: conversationId ?? undefined,
          });
        } catch (error) {
          const status = getApiErrorStatus(error);
          const staleConversation =
            conversationId !== null && (status === 409 || status === 404 || status === 403);
          if (!staleConversation) throw error;

          // The stored conversation no longer matches this group or owner. Retry in a fresh
          // conversation; the visible thread is only replaced once that retry has answered, so
          // a second failure shows the normal error on top of the existing messages.
          response = await askAiChat({ groupId, message: trimmed });
          startedFreshConversation =
            status === 409
              ? "That conversation belongs to a different group, so a new chat was started."
              : "That conversation is no longer available, so a new chat was started.";
        }

        const answer = response.answer.trim();
        const assistantMessage: ThreadMessage = {
          key: nextLocalKey("assistant"),
          role: "assistant",
          groupId,
          content: answer,
          sources: response.sources,
          evidence_status: response.evidence_status,
          reindexState: "idle",
          reindexFeedback: null,
        };
        setActiveConversationId(response.conversation_id);
        if (startedFreshConversation) {
          setNotice(startedFreshConversation);
          setMessages([userMessage, assistantMessage]);
        } else {
          setMessages((current) => [...current, assistantMessage]);
        }
        setAnnouncement(answer ? "Zabt answered." : "Zabt did not return an answer.");
        void loadConversations(groupId, { silent: true });
      } catch (error) {
        const status = getApiErrorStatus(error);
        const errorMessage = status === 503 ? UNAVAILABLE_CHAT_ERROR : GENERIC_CHAT_ERROR;
        setChatError(errorMessage);
        setAnnouncement("");
        setMessages((current) => [
          ...current,
          {
            key: nextLocalKey("error"),
            role: "assistant",
            groupId,
            content: "",
            sources: [],
            evidence_status: null,
            reindexState: "idle",
            reindexFeedback: null,
            error: errorMessage,
          },
        ]);
      } finally {
        askingRef.current = false;
        setIsAsking(false);
      }
    },
    [loadConversations],
  );

  useEffect(() => {
    let active = true;

    getGroups()
      .then((loadedGroups) => {
        if (!active) return;
        const firstGroupId = loadedGroups[0]?.id ?? null;
        setGroups(loadedGroups);
        setSelectedGroupId(firstGroupId);
        setGroupsError(null);
        if (firstGroupId !== null) {
          void loadConversations(firstGroupId);
          // `?q=` from the home search bar starts a new chat in the first group. The query is
          // consumed from the URL so a reload or back navigation does not create another one.
          if (initialQuery) {
            router.replace("/ai-chat");
            void submitQuestion(initialQuery, firstGroupId, null);
          }
        }
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
  }, [initialQuery, loadConversations, router, submitQuestion]);

  useEffect(() => {
    const list = messageListRef.current;
    if (!list) return;
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const behavior: ScrollBehavior = pendingScrollRef.current ?? (reducedMotion ? "auto" : "smooth");
    pendingScrollRef.current = null;
    list.scrollTo({ top: list.scrollHeight, behavior });
  }, [messages, isAsking, threadStatus]);

  const selectedGroup = useMemo(
    () => groups.find((group) => group.id === selectedGroupId) ?? null,
    [groups, selectedGroupId],
  );

  const activeConversation = useMemo(
    () => conversations.find((conversation) => conversation.id === activeConversationId) ?? null,
    [conversations, activeConversationId],
  );

  const startNewChat = useCallback(() => {
    threadRequestRef.current = null;
    setActiveConversationId(null);
    setMessages([]);
    setThreadStatus("idle");
    setChatError(null);
    setNotice(null);
    composerRef.current?.focus();
  }, []);

  const handleGroupChange = (groupId: number) => {
    setSelectedGroupId(groupId);
    setConversations([]);
    setDeleteError(null);
    startNewChat();
    void loadConversations(groupId);
  };

  const selectConversation = async (conversation: AIChatConversationSummary) => {
    // Re-selecting the active conversation is a no-op unless its last load failed (retry).
    const alreadyLoaded = conversation.id === activeConversationId && threadStatus !== "error";
    if (alreadyLoaded || askingRef.current) return;
    threadRequestRef.current = conversation.id;
    setActiveConversationId(conversation.id);
    setMessages([]);
    setThreadStatus("loading");
    setChatError(null);
    setNotice(null);

    try {
      const detail = await getAiChatConversation(conversation.id);
      if (threadRequestRef.current !== conversation.id) return;
      // Hydrated threads jump to the latest message instead of animating from the top.
      pendingScrollRef.current = "auto";
      setMessages(detail.messages.map((item) => fromApiMessage(item, detail.group_id)));
      setThreadStatus("idle");
      setAnnouncement(
        `Loaded conversation “${detail.title}” with ${detail.messages.length} messages.`,
      );
    } catch (error) {
      if (threadRequestRef.current !== conversation.id) return;
      setThreadStatus("error");
      const status = getApiErrorStatus(error);
      if (status === 404 || status === 403) {
        setChatError("That conversation is no longer available. The list has been refreshed.");
        if (selectedGroupId !== null) void loadConversations(selectedGroupId, { silent: true });
      } else {
        setChatError("We could not load that conversation. Please try again.");
      }
    }
  };

  const confirmDelete = async () => {
    if (!pendingDelete) return;
    const target = pendingDelete;
    setDeleting(true);
    setDeleteError(null);
    try {
      await deleteAiChatConversation(target.id);
      setConversations((current) => current.filter((conversation) => conversation.id !== target.id));
      if (target.id === activeConversationId) startNewChat();
      setPendingDelete(null);
      setAnnouncement(`Deleted conversation “${target.title}”.`);
    } catch {
      setDeleteError("We could not delete that conversation. Please try again.");
      setPendingDelete(null);
    } finally {
      setDeleting(false);
    }
  };

  const queueGroupReindex = useCallback(async (messageKey: string, groupId: number) => {
    const update = (patch: Partial<ThreadMessage>) =>
      setMessages((current) =>
        current.map((item) => (item.key === messageKey ? { ...item, ...patch } : item)),
      );

    update({ reindexState: "loading", reindexFeedback: "Refreshing the group index…" });
    try {
      await reindexGroup(groupId);
      update({
        reindexState: "success",
        reindexFeedback: "The group index refresh was queued. Ask again after it completes.",
      });
    } catch {
      update({
        reindexState: "error",
        reindexFeedback: "We could not queue the group index refresh. Please try again.",
      });
    }
  }, []);

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (selectedGroupId === null) return;
    void submitQuestion(message, selectedGroupId, activeConversationId);
  };

  const noGroups = !groupsLoading && !groupsError && groups.length === 0;
  const composerDisabled = noGroups || Boolean(groupsError) || threadStatus === "loading";
  const canSubmit =
    Boolean(message.trim()) && selectedGroupId !== null && !groupsLoading && !isAsking && !composerDisabled;
  const showEmptyThread =
    messages.length === 0 && !isAsking && threadStatus === "idle";

  return (
    <div className="flex h-full min-h-0 flex-col px-4 py-4 sm:px-8 sm:py-6">
      <div className="mb-4 flex shrink-0 flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold text-stone-900">AI Chat</h1>
          <p className="mt-1 max-w-2xl text-sm text-stone-500">
            Ask questions across one selected group. Answers are grounded in retrieved meeting snippets
            and remember earlier turns of the same conversation.
          </p>
        </div>
        <div className="inline-flex w-fit items-center gap-2 rounded-lg border border-primary/20 bg-primary/10 px-3 py-1 text-xs font-medium text-primary">
          <Bot aria-hidden="true" className="size-3.5" />
          Group-aware answers
        </div>
      </div>

      <div
        role="status"
        aria-live="polite"
        aria-atomic="true"
        className="sr-only"
      >
        {announcement}
      </div>

      <div className="grid min-h-0 flex-1 grid-rows-[auto_minmax(0,1fr)] gap-4 lg:grid-cols-[280px_minmax(0,1fr)] lg:grid-rows-1">
        <aside
          aria-labelledby="conversations-heading"
          className="flex max-h-[40dvh] min-h-0 flex-col rounded-lg border border-stone-200 bg-white lg:max-h-none"
        >
          <div className="shrink-0 border-b border-stone-200 p-4">
            <label
              htmlFor="ai-chat-group"
              className="flex items-center gap-2 text-xs font-medium uppercase tracking-wide text-stone-400"
            >
              <Users aria-hidden="true" className="size-3.5 text-primary" />
              Group
            </label>

            {groupsLoading && (
              <div className="mt-2 flex items-center gap-2 rounded-lg border border-stone-200 bg-stone-50 p-3 text-sm text-stone-500">
                <Loader2 aria-hidden="true" className="size-4 animate-spin motion-reduce:animate-none text-primary" />
                Loading groups…
              </div>
            )}

            {groupsError && (
              <div role="alert" className="mt-2 rounded-lg border border-primary/25 bg-primary/10 p-3 text-sm text-stone-700">
                <div className="flex items-start gap-2">
                  <AlertCircle aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-primary" />
                  <p>{groupsError}</p>
                </div>
              </div>
            )}

            {noGroups && (
              <p className="mt-2 rounded-lg border border-stone-200 bg-stone-50 p-3 text-sm text-stone-600">
                No groups are available yet. Add meetings to a group before asking group-level questions.
              </p>
            )}

            {!groupsLoading && groups.length > 0 && (
              <>
                <select
                  id="ai-chat-group"
                  name="group"
                  value={selectedGroupId ?? ""}
                  onChange={(event) => handleGroupChange(Number(event.target.value))}
                  disabled={isAsking}
                  className={cn(
                    "mt-2 h-8 w-full rounded-lg border border-stone-200 bg-white px-2.5 text-sm text-stone-800 disabled:cursor-not-allowed disabled:opacity-50",
                    FOCUS_RING,
                  )}
                >
                  {groups.map((group) => (
                    <option key={group.id} value={group.id}>
                      {group.name}
                    </option>
                  ))}
                </select>
                {selectedGroup?.description && (
                  <p className="mt-2 line-clamp-2 text-xs text-stone-500">{selectedGroup.description}</p>
                )}
              </>
            )}
          </div>

          <div className="flex shrink-0 items-center justify-between gap-2 px-4 pb-2 pt-3">
            <h2
              id="conversations-heading"
              className="flex items-center gap-2 text-xs font-medium uppercase tracking-wide text-stone-400"
            >
              <MessageSquare aria-hidden="true" className="size-3.5 text-primary" />
              Conversations
            </h2>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={startNewChat}
              disabled={selectedGroupId === null || isAsking}
            >
              <Plus aria-hidden="true" />
              New chat
            </Button>
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-2 pb-2">
            {deleteError && (
              <p role="alert" className="mx-2 mb-2 text-sm text-primary">
                {deleteError}
              </p>
            )}

            {conversationsStatus === "loading" && (
              <div className="flex items-center gap-2 px-2 py-3 text-sm text-stone-500">
                <Loader2 aria-hidden="true" className="size-4 animate-spin motion-reduce:animate-none text-primary" />
                Loading conversations…
              </div>
            )}

            {conversationsStatus === "error" && selectedGroupId !== null && (
              <div role="alert" className="mx-2 rounded-lg border border-primary/25 bg-primary/10 p-3 text-sm text-stone-700">
                <p>We could not load the conversations for this group.</p>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="mt-2"
                  onClick={() => void loadConversations(selectedGroupId)}
                >
                  <RefreshCw aria-hidden="true" />
                  Retry
                </Button>
              </div>
            )}

            {conversationsStatus === "idle" && selectedGroupId !== null && conversations.length === 0 && (
              <p className="px-2 py-3 text-sm text-stone-500">
                No conversations yet. Ask a question to start one.
              </p>
            )}

            {conversations.length > 0 && (
              <ul className="space-y-1" aria-label="Saved conversations">
                {conversations.map((conversation) => {
                  const isActive = conversation.id === activeConversationId;
                  return (
                    <li
                      key={conversation.id}
                      className={cn(
                        "flex items-center gap-1 rounded-lg border",
                        isActive
                          ? "border-primary/40 bg-primary/10"
                          : "border-transparent hover:bg-stone-50",
                      )}
                    >
                      <button
                        type="button"
                        onClick={() => void selectConversation(conversation)}
                        aria-current={isActive ? "true" : undefined}
                        disabled={isAsking}
                        className={cn(
                          "min-w-0 flex-1 rounded-lg px-3 py-2 text-left disabled:cursor-not-allowed disabled:opacity-60",
                          FOCUS_RING,
                        )}
                      >
                        <span className="block truncate text-sm font-medium text-stone-800">
                          {conversation.title || "Untitled conversation"}
                        </span>
                        <span className="mt-0.5 block text-xs tabular-nums text-stone-500">
                          {conversation.message_count}{" "}
                          {conversation.message_count === 1 ? "message" : "messages"} ·{" "}
                          {updatedAtFormatter.format(new Date(conversation.updated_at))}
                        </span>
                      </button>
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon-sm"
                        aria-label={`Delete conversation “${conversation.title || "Untitled conversation"}”`}
                        onClick={() => setPendingDelete(conversation)}
                        disabled={isAsking}
                        className="mr-1 shrink-0 text-stone-400 hover:text-destructive"
                      >
                        <Trash2 aria-hidden="true" />
                      </Button>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        </aside>

        <section
          aria-labelledby="chat-heading"
          className="flex min-h-0 flex-col rounded-lg border border-stone-200 bg-white"
        >
          <div className="shrink-0 border-b border-stone-200 px-5 py-4">
            <h2 id="chat-heading" className="text-lg font-semibold text-stone-900">
              {activeConversation ? activeConversation.title : "Ask Zabt"}
            </h2>
            <p className="mt-0.5 truncate text-sm text-stone-500">
              {selectedGroup
                ? activeConversation
                  ? `Conversation in ${selectedGroup.name}`
                  : `New chat in ${selectedGroup.name}`
                : "Select a group to start chatting."}
            </p>
          </div>

          <div
            ref={messageListRef}
            className="min-h-0 flex-1 space-y-4 overflow-y-auto overscroll-contain p-5"
          >
            {notice && (
              <p role="status" className="rounded-lg border border-stone-200 bg-stone-50 px-3 py-2 text-sm text-stone-600">
                {notice}
              </p>
            )}

            {threadStatus === "loading" && (
              <div className="flex items-center gap-2 rounded-lg border border-stone-200 bg-stone-50 p-4 text-sm text-stone-600">
                <Loader2 aria-hidden="true" className="size-4 animate-spin motion-reduce:animate-none text-primary" />
                Loading conversation…
              </div>
            )}

            {showEmptyThread && (
              <div className="flex min-h-[260px] flex-col items-center justify-center rounded-lg border border-dashed border-stone-200 bg-stone-50 p-6 text-center">
                <Search aria-hidden="true" className="mb-3 size-6 text-primary" />
                <p className="text-sm font-medium text-stone-800">Ask a question about the selected group.</p>
                <p className="mt-1 max-w-md text-pretty text-sm text-stone-500">
                  Try asking for decisions, risks, open questions, or themes across meetings in that group.
                  Follow-up questions remember the earlier turns of this conversation.
                </p>
              </div>
            )}

            {messages.map((item) =>
              item.role === "user" ? (
                <UserMessage key={item.key} message={item} />
              ) : (
                <AssistantMessage key={item.key} message={item} onReindex={queueGroupReindex} />
              ),
            )}

            {isAsking && (
              <div className="flex items-center gap-2 rounded-lg border border-stone-200 bg-stone-50 p-4 text-sm text-stone-600">
                <Loader2 aria-hidden="true" className="size-4 animate-spin motion-reduce:animate-none text-primary" />
                Asking Zabt…
              </div>
            )}
          </div>

          <form onSubmit={handleSubmit} className="shrink-0 border-t border-stone-200 p-4">
            {chatError && (
              <p role="alert" className="mb-3 text-sm text-primary">
                {chatError}
              </p>
            )}
            <label htmlFor="ai-chat-message" className="sr-only">
              Ask a question about the selected group
            </label>
            <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
              <textarea
                ref={composerRef}
                id="ai-chat-message"
                name="message"
                value={message}
                onChange={(event) => setMessage(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    if (canSubmit && selectedGroupId !== null) {
                      void submitQuestion(message, selectedGroupId, activeConversationId);
                    }
                  }
                }}
                rows={2}
                autoComplete="off"
                placeholder="Ask about decisions, blockers, or themes…"
                disabled={composerDisabled}
                className="min-h-12 max-h-40 flex-1 resize-y rounded-lg border border-stone-200 bg-white px-3 py-2 text-sm text-stone-800 placeholder-stone-400 transition-colors focus:border-primary/40 focus:outline-none focus:ring-2 focus:ring-primary/20 disabled:cursor-not-allowed disabled:bg-stone-100 disabled:text-stone-400"
              />
              <Button type="submit" size="lg" disabled={!canSubmit} className="px-4">
                {isAsking ? (
                  <Loader2 aria-hidden="true" className="animate-spin motion-reduce:animate-none" />
                ) : (
                  <ArrowRight aria-hidden="true" />
                )}
                Ask
              </Button>
            </div>
            <p className="mt-2 text-xs text-stone-400">
              Press Enter to submit, or Shift+Enter for a new line.
            </p>
          </form>
        </section>
      </div>

      <AlertDialog
        open={pendingDelete !== null}
        onOpenChange={(open) => {
          if (!open && !deleting) setPendingDelete(null);
        }}
      >
        <AlertDialogContent size="sm">
          <AlertDialogHeader>
            <AlertDialogTitle>Delete this conversation?</AlertDialogTitle>
            <AlertDialogDescription>
              “{pendingDelete?.title || "Untitled conversation"}” and all of its messages will be removed.
              This action cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleting}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              loading={deleting}
              onClick={() => void confirmDelete()}
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
