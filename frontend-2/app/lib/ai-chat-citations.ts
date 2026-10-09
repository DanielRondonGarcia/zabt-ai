// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2025-2026 Afeef Janjua
import type { AIChatSource } from "@/app/lib/api";

/** Inline citation emitted by the model, e.g. `[meeting:12 kind:summary chunk:3]`. */
export const CITATION_TOKEN_RE = /\[meeting:(\d+)\s+kind:([a-zA-Z0-9_-]+)\s+chunk:(\d+)\]/g;

/** Hash prefix used to smuggle citations through Markdown links; see `ChatMarkdown`. */
export const CITATION_HREF_PREFIX = "#cite/";

export interface CitationRef {
  meeting_id: number;
  kind: string;
  chunk_index: number;
}

export interface NumberedCitation extends CitationRef {
  /** 1-based reference number shared by inline badges and the sources list. */
  number: number;
  /** Snippet text when the citation matches a returned source. */
  text: string | null;
}

export interface MeetingSourceGroup {
  meeting_id: number;
  /** Ordered kind counts, e.g. `[["summary", 1], ["transcript", 6]]`. */
  kindCounts: Array<[string, number]>;
  citations: NumberedCitation[];
}

export const citationKey = ({ meeting_id, kind, chunk_index }: CitationRef): string =>
  `${meeting_id}:${kind}:${chunk_index}`;

export const citationHref = (ref: CitationRef): string =>
  `${CITATION_HREF_PREFIX}${ref.meeting_id}/${encodeURIComponent(ref.kind)}/${ref.chunk_index}`;

const DIGITS_RE = /^\d+$/;
/** Same charset the citation token accepts for `kind`. */
const KIND_RE = /^[a-zA-Z0-9_-]+$/;

/**
 * Parses a `#cite/<meeting>/<kind>/<chunk>` href. Returns `null` for anything malformed so a
 * hostile or corrupted link can never throw during render: ids must be plain decimal digits
 * (meeting id positive, chunk index zero-based and non-negative) and the kind must decode to
 * the token charset.
 */
export const parseCitationHref = (href: string | undefined): CitationRef | null => {
  if (!href || !href.startsWith(CITATION_HREF_PREFIX)) return null;
  const segments = href.slice(CITATION_HREF_PREFIX.length).split("/");
  if (segments.length !== 3) return null;
  const [meetingId, encodedKind, chunkIndex] = segments;
  if (!DIGITS_RE.test(meetingId) || !DIGITS_RE.test(chunkIndex) || !encodedKind) return null;

  const meeting_id = Number(meetingId);
  const chunk_index = Number(chunkIndex);
  if (!Number.isSafeInteger(meeting_id) || meeting_id <= 0) return null;
  if (!Number.isSafeInteger(chunk_index) || chunk_index < 0) return null;

  let kind: string;
  try {
    kind = decodeURIComponent(encodedKind);
  } catch {
    return null;
  }
  if (!KIND_RE.test(kind)) return null;

  return { meeting_id, kind, chunk_index };
};

const FENCE_LINE_RE = /^\s*(```|~~~)/;
const INDENTED_CODE_RE = /^(?: {4}|\t)/;
/** Inline code span: a backtick run closed by a run of the same length. */
const INLINE_CODE_RE = /(`+)[^`][\s\S]*?\1(?!`)/g;

/**
 * Applies `transform` to prose text only. Fenced code blocks, indented code blocks, and inline
 * code spans pass through unchanged so diagram and code sources are never rewritten.
 */
const mapProseSegments = (answer: string, transform: (segment: string) => string): string => {
  let insideFence = false;
  return answer
    .split("\n")
    .map((line) => {
      if (FENCE_LINE_RE.test(line)) {
        insideFence = !insideFence;
        return line;
      }
      if (insideFence || INDENTED_CODE_RE.test(line)) return line;

      let output = "";
      let cursor = 0;
      for (const match of line.matchAll(INLINE_CODE_RE)) {
        const start = match.index ?? 0;
        output += transform(line.slice(cursor, start)) + match[0];
        cursor = start + match[0].length;
      }
      return output + transform(line.slice(cursor));
    })
    .join("\n");
};

/**
 * Builds a single numbering shared by the answer's inline citations and its sources list.
 * Returned sources are numbered first and deduplicated in API order. Model tokens that do not
 * match a returned source are intentionally not added to the index.
 */
export const buildCitationIndex = (
  sources: AIChatSource[],
): Map<string, NumberedCitation> => {
  const index = new Map<string, NumberedCitation>();

  for (const source of sources) {
    const key = citationKey(source);
    if (index.has(key)) continue;
    index.set(key, {
      meeting_id: source.meeting_id,
      kind: source.kind,
      chunk_index: source.chunk_index,
      number: index.size + 1,
      text: source.text,
    });
  }

  return index;
};

/**
 * Rewrites inline citation tokens as Markdown links with a `#cite/...` href so the Markdown
 * renderer can turn them into badges. Fenced, indented, and inline code are left untouched.
 */
export const linkifyCitations = (
  answer: string,
  index: Map<string, NumberedCitation>,
): string =>
  mapProseSegments(answer, (segment) =>
    segment.replace(CITATION_TOKEN_RE, (token: string, meetingId: string, kind: string, chunk: string) => {
      const ref: CitationRef = {
        meeting_id: Number(meetingId),
        kind,
        chunk_index: Number(chunk),
      };
      const citation = index.get(citationKey(ref));
      if (!citation) return token;
      // Escaped brackets keep the visible `[n]` label from being parsed as a nested link.
      const label = `\\[${citation.number}\\]`;
      return `[${label}](${citationHref(ref)})`;
    }),
  );

/** Groups numbered citations by meeting, preserving first-seen order and counting kinds. */
export const groupCitationsByMeeting = (
  index: Map<string, NumberedCitation>,
): MeetingSourceGroup[] => {
  const groups = new Map<number, MeetingSourceGroup>();

  for (const citation of index.values()) {
    let group = groups.get(citation.meeting_id);
    if (!group) {
      group = { meeting_id: citation.meeting_id, kindCounts: [], citations: [] };
      groups.set(citation.meeting_id, group);
    }
    group.citations.push(citation);
    const entry = group.kindCounts.find(([kind]) => kind === citation.kind);
    if (entry) {
      entry[1] += 1;
    } else {
      group.kindCounts.push([citation.kind, 1]);
    }
  }

  return Array.from(groups.values());
};

export const formatKindCounts = (kindCounts: Array<[string, number]>): string =>
  kindCounts.map(([kind, count]) => `${kind} ×${count}`).join(" · ");
