// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2025-2026 Afeef Janjua
"use client";

import type { Element as HastElement, ElementContent } from "hast";
import Link from "next/link";
import { useMemo } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

import { MermaidDiagram } from "@/app/components/mermaid-diagram";
import {
  citationKey,
  linkifyCitations,
  parseCitationHref,
  type NumberedCitation,
} from "@/app/lib/ai-chat-citations";
import { cn } from "@/app/lib/utils";

const FOCUS_RING =
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:ring-offset-1";

const isElement = (node: ElementContent): node is HastElement => node.type === "element";

/** Reads `language-xxx` from a hast `code` element's class list. */
const codeLanguage = (code: HastElement): string | null => {
  const className: unknown = code.properties?.className;
  const classes: string[] =
    typeof className === "string"
      ? className.split(/\s+/)
      : Array.isArray(className)
        ? className.map(String)
        : [];
  const match = classes.find((value) => value.startsWith("language-"));
  return match ? match.slice("language-".length) : null;
};

const textContent = (node: ElementContent): string => {
  if (node.type === "text") return node.value;
  if (node.type === "element") return node.children.map(textContent).join("");
  return "";
};

interface CitationBadgeProps {
  citation: NumberedCitation;
  meetingId: number;
  kind: string;
  chunkIndex: number;
}

function CitationBadge({ citation, meetingId, kind, chunkIndex }: CitationBadgeProps) {
  const label = String(citation.number);
  const description = `Meeting ${meetingId} · ${kind} · chunk ${chunkIndex + 1}`;

  return (
    <Link
      href={`/meetings/${meetingId}`}
      title={description}
      aria-label={`Source ${label}: ${description}`}
      className={cn(
        "not-prose mx-0.5 inline-flex h-5 min-w-5 items-center justify-center rounded-4xl border border-primary/30 bg-primary/10 px-1.5 align-text-bottom text-xs font-medium leading-none text-primary no-underline transition-colors hover:bg-primary/20",
        FOCUS_RING,
      )}
    >
      {label}
    </Link>
  );
}

interface ChatMarkdownProps {
  content: string;
  /** Shared numbering built with `buildCitationIndex`; omit to render citations as plain text. */
  citations?: Map<string, NumberedCitation>;
  className?: string;
}

export function ChatMarkdown({ content, citations, className }: ChatMarkdownProps) {
  const markdown = useMemo(
    () => (citations ? linkifyCitations(content, citations) : content),
    [content, citations],
  );

  const components = useMemo<Components>(
    () => ({
      // Model headings are demoted two levels so they sit below the page `h1`/`h2` hierarchy.
      // eslint-disable-next-line @typescript-eslint/no-unused-vars
      h1: ({ node: _node, ...props }) => <h3 {...props} />,
      // eslint-disable-next-line @typescript-eslint/no-unused-vars
      h2: ({ node: _node, ...props }) => <h4 {...props} />,
      // eslint-disable-next-line @typescript-eslint/no-unused-vars
      h3: ({ node: _node, ...props }) => <h5 {...props} />,
      // eslint-disable-next-line @typescript-eslint/no-unused-vars
      h4: ({ node: _node, ...props }) => <h6 {...props} />,
      // eslint-disable-next-line @typescript-eslint/no-unused-vars
      h5: ({ node: _node, ...props }) => <h6 {...props} />,
      // `node` is the hast element react-markdown passes along; it must not reach the DOM.
      // eslint-disable-next-line @typescript-eslint/no-unused-vars
      a: ({ href, children, node: _node, ...props }) => {
        const ref = parseCitationHref(href);
        if (ref) {
          const citation = citations?.get(citationKey(ref));
          if (!citation) return <>{children}</>;
          return (
            <CitationBadge
              citation={citation}
              meetingId={ref.meeting_id}
              kind={ref.kind}
              chunkIndex={ref.chunk_index}
            />
          );
        }
        return (
          <a
            href={href}
            target="_blank"
            rel="noreferrer noopener"
            className={cn("break-words text-primary underline underline-offset-2", FOCUS_RING)}
            {...props}
          >
            {children}
          </a>
        );
      },
      pre: ({ children, node, ...props }) => {
        const code = node?.children.find(isElement);
        const language = code && code.tagName === "code" ? codeLanguage(code) : null;

        if (code && language === "mermaid") {
          return <MermaidDiagram code={textContent(code)} />;
        }

        return (
          <div className="not-prose my-4 min-w-0 overflow-hidden rounded-lg border border-stone-200 bg-stone-50 dark:border-border dark:bg-muted">
            {language && (
              <div className="border-b border-stone-200 px-4 py-1.5 font-mono text-xs text-stone-500 dark:border-border dark:text-muted-foreground">
                {language}
              </div>
            )}
            <pre
              className="max-w-full overflow-x-auto p-4 font-mono text-xs leading-5 text-stone-800 dark:text-foreground [&_code]:bg-transparent [&_code]:p-0 [&_code]:text-inherit"
              {...props}
            >
              {children}
            </pre>
          </div>
        );
      },
      // eslint-disable-next-line @typescript-eslint/no-unused-vars
      table: ({ children, node: _node, ...props }) => (
        <div className="my-4 min-w-0 max-w-full overflow-x-auto rounded-lg border border-stone-200 dark:border-border">
          <table className="my-0 min-w-full" {...props}>
            {children}
          </table>
        </div>
      ),
    }),
    [citations],
  );

  return (
    <div
      className={cn(
        "prose prose-stone prose-sm max-w-none break-words",
        "prose-headings:font-semibold prose-headings:text-stone-900 prose-p:text-stone-800",
        "prose-a:text-primary prose-strong:text-stone-900 dark:prose-strong:text-foreground",
        "prose-code:rounded-md prose-code:bg-stone-100 prose-code:px-1 prose-code:py-0.5 prose-code:font-mono prose-code:font-normal prose-code:text-stone-800 prose-code:before:content-none prose-code:after:content-none dark:prose-code:bg-muted dark:prose-code:text-foreground",
        "prose-th:text-stone-700 prose-td:align-top",
        className,
      )}
    >
      <ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml components={components}>
        {markdown}
      </ReactMarkdown>
    </div>
  );
}
