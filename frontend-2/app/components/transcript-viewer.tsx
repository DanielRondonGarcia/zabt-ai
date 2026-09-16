// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2025-2026 Afeef Janjua
"use client";

import { memo } from "react";
import type { TranscriptSegment, TranscriptWord } from "@/app/lib/api";
import { useTranscriptStore } from "@/app/lib/use-transcript-store";
import { UserRound } from "lucide-react";

const formatTime = (seconds: number) => {
    if (isNaN(seconds) || seconds < 0) return "0:00";
    const m = Math.floor(seconds / 60);
    const s = Math.floor(seconds % 60);
    return `${m}:${s.toString().padStart(2, "0")}`;
};

const SPEAKER_COLORS: Record<string, string> = {
    SPEAKER_00: "bg-stone-500 text-white",
    SPEAKER_01: "bg-stone-600 text-white",
    SPEAKER_02: "bg-stone-700 text-white",
    SPEAKER_03: "bg-stone-400 text-stone-950",
};

const getSpeakerColor = (speaker: string) =>
    SPEAKER_COLORS[speaker] ?? "bg-stone-200 text-stone-500";

const getSpeakerLabel = (speaker: string) => {
    if (speaker === "SPEAKER_UNKNOWN") return "Unknown Speaker";
    const num = speaker.split("_")[1];
    return num !== undefined ? `Speaker ${parseInt(num) + 1}` : speaker;
};

const WordSpan = memo(({ word }: { word: TranscriptWord }) => {
    const isHighlighted = useTranscriptStore((state) =>
        state.currentTime >= word.start && state.currentTime <= word.end
    );
    const { setSeekRequest } = useTranscriptStore();

    return (
        <button
            type="button"
            aria-label={`Seek to ${formatTime(word.start)}`}
            className={`mr-0.5 inline rounded px-0.5 text-left transition-colors duration-75 motion-reduce:transition-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/50 ${
                isHighlighted ? "bg-primary text-primary-foreground" : "hover:bg-accent"
            }`}
            onClick={() => setSeekRequest(word.start)}
        >
            {word.word}
        </button>
    );
});

WordSpan.displayName = "WordSpan";

function SegmentRow({
    segment,
    isPaywalled,
}: {
    segment: TranscriptSegment;
    isPaywalled: boolean;
}) {
    const { setSeekRequest } = useTranscriptStore();
    const currentTime = useTranscriptStore((state) => state.currentTime);
    const words = Array.isArray(segment.words) ? segment.words : [];
    const isUnknown = segment.speaker === "SPEAKER_UNKNOWN";
    const speakerLabel = getSpeakerLabel(segment.speaker);
    const isActive = currentTime >= segment.start && currentTime < segment.end;

    return (
        <article
            aria-current={isActive ? "true" : undefined}
            data-active={isActive ? "true" : "false"}
            className={`flex gap-3 border-b border-border py-4 last:border-none ${
                isActive ? "rounded-lg border-l-2 border-l-primary bg-primary/5 pl-3" : ""
            } ${
                isPaywalled ? "pointer-events-none select-none opacity-50 blur-sm" : ""
            }`}
        >
            {/* Avatar */}
            <div className="flex-shrink-0 pt-0.5">
                <div
                    className={`flex size-8 items-center justify-center rounded-full text-sm font-semibold ${getSpeakerColor(segment.speaker)}`}
                >
                    {isUnknown ? (
                        <UserRound className="size-3.5" aria-hidden="true" />
                    ) : (
                        speakerLabel.charAt(speakerLabel.indexOf(" ") + 1)
                    )}
                </div>
            </div>

            {/* Content */}
            <div className="flex-1 min-w-0">
                <div className="flex items-baseline gap-2 mb-1">
                    <span className="text-sm font-medium text-foreground">
                        {speakerLabel}
                    </span>
                    <button
                        type="button"
                        aria-label={`Seek to ${formatTime(segment.start)}`}
                        className="touch-manipulation cursor-pointer text-xs tabular-nums text-muted-foreground transition-colors motion-reduce:transition-none hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/50"
                        onClick={() => setSeekRequest(segment.start)}
                    >
                        {formatTime(segment.start)}
                    </button>
                </div>
                <p className="text-sm leading-relaxed text-foreground">
                    {words.length > 0 ? (
                        words.map((w, i) => <WordSpan key={i} word={w} />)
                    ) : (
                        segment.text
                    )}
                </p>
            </div>
        </article>
    );
}

export function TranscriptViewer({
    segments,
    transcriptText,
    isFreeTier = false,
}: {
    segments: TranscriptSegment[];
    transcriptText?: string | null;
    isFreeTier?: boolean;
}) {
    if (!segments || segments.length === 0) {
        if (transcriptText?.trim()) {
            return (
                <article className="space-y-3 py-4">
                    <div className="rounded-lg border border-border bg-muted/50 px-3.5 py-2.5">
                        <p className="text-xs leading-relaxed text-muted-foreground">
                            Text transcript without timestamped segments. Seeking is unavailable.
                        </p>
                    </div>
                    <p className="break-words whitespace-pre-wrap text-sm leading-relaxed text-foreground">
                        {transcriptText}
                    </p>
                </article>
            );
        }
        return <p className="py-4 text-sm text-muted-foreground">No transcript available.</p>;
    }

    return (
        <div className="min-w-0">
            {[...segments].sort((a, b) => a.start - b.start).map((segment, index) => (
                <SegmentRow
                    key={index}
                    segment={segment}
                    isPaywalled={isFreeTier && segment.start >= 1800}
                />
            ))}
        </div>
    );
}
