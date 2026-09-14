// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2025-2026 Afeef Janjua
"use client";

import { useCallback, useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";
import { useTranscriptStore } from "@/app/lib/use-transcript-store";
import type { Meeting } from "@/app/lib/api";
import { AudioLines, Download, Pause, Play, RotateCcw } from "lucide-react";

interface StickyMediaPlayerProps {
    meeting: Meeting;
}

const MEDIA_ERROR_MESSAGE = "This media could not be loaded. Transcript seeking is still available.";

const getDownloadFilename = (title: string, mediaSrc: string | undefined) => {
    const safeTitle = title.replace(/[^a-z0-9\s_-]/gi, "").trim().slice(0, 80) || "meeting";
    const extension = mediaSrc?.match(/\.(mp4|webm|mov|m4v)(?:$|\?)/i)?.[1]?.toLowerCase() ?? "mp4";
    return `${safeTitle}.${extension}`;
};

export function StickyMediaPlayer({ meeting }: StickyMediaPlayerProps) {
    const mediaRef = useRef<HTMLMediaElement>(null);
    const rafRef = useRef<number | null>(null);
    const setMediaRef = useCallback((element: HTMLMediaElement | null) => {
        mediaRef.current = element;
    }, []);
    const {
        currentTime,
        duration,
        isPlaying,
        seekRequest,
        setCurrentTime,
        setDuration,
        setIsPlaying,
        setSeekRequest,
        reset,
    } = useTranscriptStore();

    const [playbackRate, setPlaybackRate] = useState(1);
    const [mediaError, setMediaError] = useState<string | null>(null);
    const [isMediaLoading, setIsMediaLoading] = useState(true);
    const mediaType = meeting.media_type === "video" ? "video" : "audio";
    const mediaSrc = meeting.audio_url || meeting.file_path || undefined;
    const mediaKey = `${meeting.id}:${mediaSrc ?? ""}:${mediaType}`;
    const mediaDescriptionId = `meeting-${meeting.id}-media-description`;
    const timelineId = `meeting-${meeting.id}-timeline`;
    const downloadFilename = getDownloadFilename(meeting.title, mediaSrc);
    const progressPercent = duration > 0
        ? Math.min(100, Math.max(0, (currentTime / duration) * 100))
        : 0;

    const stopProgressLoop = useCallback(() => {
        if (rafRef.current !== null) {
            cancelAnimationFrame(rafRef.current);
            rafRef.current = null;
        }
    }, []);

    const updateProgress = useCallback(() => {
        const animate = () => {
            const media = mediaRef.current;
            if (!media || media.paused || media.ended) {
                rafRef.current = null;
                return;
            }

            setCurrentTime(media.currentTime);
            rafRef.current = requestAnimationFrame(animate);
        };

        animate();
    }, [setCurrentTime]);

    // Reset shared playback state whenever the media identity changes.
    useEffect(() => {
        const media = mediaRef.current;
        reset(mediaKey);
        stopProgressLoop();

        if (!media) {
            return () => {
                if (useTranscriptStore.getState().mediaKey === mediaKey) reset(null);
            };
        }

        media.pause();
        try {
            media.currentTime = 0;
        } catch {
            // A media element without a source cannot accept currentTime yet.
        }
        media.playbackRate = 1;

        const handleLoadStart = () => {
            setIsMediaLoading(true);
            setMediaError(null);
        };
        const handleLoadedMetadata = () => {
            setIsMediaLoading(false);
            if (Number.isFinite(media.duration) && media.duration > 0) {
                setDuration(media.duration);
            }
        };
        const handlePlay = () => {
            setIsPlaying(true);
            updateProgress();
        };
        const handlePause = () => {
            setIsPlaying(false);
            stopProgressLoop();
            setCurrentTime(media.currentTime);
        };
        const handleEnded = () => {
            setIsPlaying(false);
            stopProgressLoop();
            setCurrentTime(media.currentTime);
        };
        const handleTimeUpdate = () => setCurrentTime(media.currentTime);
        const handleError = () => {
            setIsMediaLoading(false);
            setIsPlaying(false);
            stopProgressLoop();
            setMediaError(MEDIA_ERROR_MESSAGE);
        };

        media.addEventListener("loadstart", handleLoadStart);
        media.addEventListener("loadedmetadata", handleLoadedMetadata);
        media.addEventListener("play", handlePlay);
        media.addEventListener("pause", handlePause);
        media.addEventListener("ended", handleEnded);
        media.addEventListener("timeupdate", handleTimeUpdate);
        media.addEventListener("error", handleError);

        if (media.readyState >= 1) handleLoadedMetadata();

        return () => {
            media.pause();
            stopProgressLoop();
            media.removeEventListener("loadstart", handleLoadStart);
            media.removeEventListener("loadedmetadata", handleLoadedMetadata);
            media.removeEventListener("play", handlePlay);
            media.removeEventListener("pause", handlePause);
            media.removeEventListener("ended", handleEnded);
            media.removeEventListener("timeupdate", handleTimeUpdate);
            media.removeEventListener("error", handleError);
            if (useTranscriptStore.getState().mediaKey === mediaKey) reset(null);
        };
    }, [
        mediaKey,
        reset,
        setCurrentTime,
        setDuration,
        setIsPlaying,
        stopProgressLoop,
        updateProgress,
    ]);

    useEffect(() => {
        if (duration === 0 && meeting.duration_seconds && meeting.duration_seconds > 0) {
            setDuration(meeting.duration_seconds);
        }
    }, [duration, meeting.duration_seconds, setDuration]);

    // Sync seek requests from transcript words and timestamps to the native player.
    useEffect(() => {
        const media = mediaRef.current;
        if (seekRequest === null || !media) return;

        try {
            media.currentTime = Math.max(0, seekRequest);
            setCurrentTime(media.currentTime);
            setSeekRequest(null);
        } catch {
            // Keep the request until the media element can accept a seek.
        }
    }, [seekRequest, setCurrentTime, setSeekRequest]);

    const togglePlay = () => {
        const media = mediaRef.current;
        if (media) {
            if (media.paused) {
                void media.play().catch(() => {
                    if (mediaRef.current === media) {
                        setIsMediaLoading(false);
                        setIsPlaying(false);
                        setMediaError(MEDIA_ERROR_MESSAGE);
                    }
                });
            } else {
                media.pause();
            }
        }
    };

    const seekTo = useCallback((time: number) => {
        if (duration <= 0 || !Number.isFinite(time)) return;
        setSeekRequest(Math.min(duration, Math.max(0, time)));
    }, [duration, setSeekRequest]);

    const handleRewind = () => {
        const media = mediaRef.current;
        if (media) {
            try {
                media.currentTime = Math.max(0, media.currentTime - 10);
                setCurrentTime(media.currentTime);
            } catch {
                // The media may not have loaded enough metadata to seek yet.
            }
        }
    };

    const handleMediaKeyDown = (event: ReactKeyboardEvent<HTMLMediaElement>) => {
        if (event.key === " " || event.key.toLowerCase() === "k") {
            event.preventDefault();
            togglePlay();
            return;
        }

        if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
            event.preventDefault();
            const direction = event.key === "ArrowLeft" ? -1 : 1;
            seekTo(currentTime + direction * (event.shiftKey ? 10 : 5));
        }
    };

    const toggleSpeed = () => {
        const nextSpeed = playbackRate === 1 ? 1.5 : playbackRate === 1.5 ? 2 : 1;
        setPlaybackRate(nextSpeed);
        if (mediaRef.current) {
            mediaRef.current.playbackRate = nextSpeed;
        }
    };

    const getSpeakerColor = (speaker: string) => {
        if (speaker === "SPEAKER_00") return "bg-stone-500";
        if (speaker === "SPEAKER_01") return "bg-stone-600";
        if (speaker === "SPEAKER_02") return "bg-stone-700";
        return "bg-stone-400";
    };

    const formatTime = (secs: number) => {
        if (!Number.isFinite(secs) || secs < 0) return "00:00";
        const m = Math.floor(secs / 60);
        const s = Math.floor(secs % 60);
        return `${m.toString().padStart(2, "0")}:${s.toString().padStart(2, "0")}`;
    };

    const mediaStatus = mediaError
        ? "Unavailable"
        : isMediaLoading
            ? "Loading…"
            : isPlaying
                ? "Playing"
                : "Ready to play";

    return (
        <div
            className="min-w-0 space-y-3"
            data-testid="meeting-media-player"
            aria-busy={isMediaLoading}
        >
            <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border bg-muted/40 px-3 py-2">
                <div className="flex min-w-0 items-center gap-2">
                    <span className="size-2 shrink-0 rounded-full bg-primary" aria-hidden="true" />
                    <span className="truncate text-sm font-medium text-foreground">
                        {mediaType === "video" ? "Video recording" : "Audio recording"}
                    </span>
                    <span className="text-xs text-muted-foreground" role="status" aria-live="polite">
                        {mediaStatus}
                    </span>
                </div>

                {mediaType === "video" && mediaSrc && (
                    <a
                        href={mediaSrc}
                        download={downloadFilename}
                        target="_blank"
                        rel="noopener noreferrer"
                        aria-label="Download video (opens in a new tab if direct download is unavailable)"
                        className="inline-flex min-h-9 shrink-0 touch-manipulation items-center gap-2 rounded-lg border border-border bg-background px-3 text-sm font-medium text-foreground transition-colors motion-reduce:transition-none hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/50"
                    >
                        <Download className="size-4" aria-hidden="true" />
                        Download video
                    </a>
                )}
            </div>

            {mediaType === "video" ? (
                <div className="overflow-hidden rounded-lg border border-stone-800 bg-stone-950">
                    <video
                        key={mediaKey}
                        ref={setMediaRef}
                        src={mediaSrc}
                        preload="metadata"
                        playsInline
                        tabIndex={0}
                        aria-label={`${meeting.title} video recording`}
                        aria-describedby={mediaDescriptionId}
                        onKeyDown={handleMediaKeyDown}
                        className="block aspect-video w-full max-h-[70vh] object-contain focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/70 focus-visible:ring-inset"
                    />
                </div>
            ) : (
                <>
                    <div className="flex min-h-56 items-center justify-center rounded-lg border border-border bg-muted/40 px-6 py-12 text-center">
                        <div className="flex flex-col items-center gap-3">
                            <div className="flex size-14 items-center justify-center rounded-full border border-border bg-background text-muted-foreground">
                                <AudioLines className="size-7" aria-hidden="true" />
                            </div>
                            <div>
                                <p className="text-sm font-semibold text-foreground">Audio recording</p>
                                <p className="mt-1 text-sm text-muted-foreground">Use the controls below or select a transcript timestamp to seek.</p>
                            </div>
                        </div>
                    </div>
                    <audio
                        key={mediaKey}
                        ref={setMediaRef}
                        src={mediaSrc}
                        preload="metadata"
                        tabIndex={-1}
                        aria-label={`${meeting.title} audio recording`}
                        aria-describedby={mediaDescriptionId}
                        onKeyDown={handleMediaKeyDown}
                        className="sr-only"
                    />
                </>
            )}

            <p id={mediaDescriptionId} className="sr-only">
                {mediaType === "video" ? "Video recording." : "Audio recording."} Use the play, rewind, speed, and timeline controls. {mediaType === "video" ? "When the video is focused, press Space or K to play or pause; use the arrow keys to seek." : "Transcript timestamps and the controls below are keyboard accessible."}
            </p>

            {mediaError && (
                <div role="alert" aria-live="assertive" className="rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">
                    {mediaError}
                </div>
            )}

            <div className="space-y-1">
                <div className="flex items-center justify-between gap-3">
                    <label htmlFor={timelineId} className="text-xs font-medium text-muted-foreground">
                        Playback timeline
                    </label>
                    <span className="shrink-0 font-mono text-xs tabular-nums text-muted-foreground" aria-hidden="true">
                        {formatTime(currentTime)} / {formatTime(duration)}
                    </span>
                </div>

                <div className="relative flex h-8 items-center">
                    <div className="pointer-events-none absolute inset-x-0 h-2 overflow-hidden rounded-full bg-muted" aria-hidden="true">
                        {duration > 0 && meeting.segments?.map((seg, i) => {
                            const left = (seg.start / duration) * 100;
                            const width = ((seg.end - seg.start) / duration) * 100;
                            return (
                                <div
                                    key={i}
                                    className={`absolute inset-y-0 ${getSpeakerColor(seg.speaker)} opacity-30`}
                                    style={{ left: `${left}%`, width: `${width}%` }}
                                />
                            );
                        })}

                        <div
                            className="absolute inset-y-0 left-0 bg-primary transition-[width] duration-75 motion-reduce:transition-none"
                            style={{ width: `${progressPercent}%` }}
                        />
                    </div>

                    <input
                        id={timelineId}
                        type="range"
                        min="0"
                        max={duration > 0 ? duration : 1}
                        step="0.1"
                        value={duration > 0 ? Math.min(currentTime, duration) : 0}
                        onChange={(event) => seekTo(Number(event.currentTarget.value))}
                        disabled={duration <= 0}
                        aria-label="Seek through recording"
                        aria-valuetext={`${formatTime(currentTime)} of ${formatTime(duration)}`}
                        className="relative z-10 h-8 w-full cursor-pointer appearance-none bg-transparent accent-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/50 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
                    />
                </div>
            </div>

            <div className="flex flex-wrap items-center gap-3">
                <button
                    type="button"
                    aria-label="Rewind 10 seconds"
                    onClick={handleRewind}
                    className="inline-flex min-h-9 min-w-9 touch-manipulation items-center justify-center rounded-lg text-muted-foreground transition-colors motion-reduce:transition-none hover:bg-accent hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/50"
                >
                    <RotateCcw className="size-5" aria-hidden="true" />
                </button>

                <button
                    type="button"
                    aria-label={isPlaying ? "Pause recording" : "Play recording"}
                    aria-pressed={isPlaying}
                    onClick={togglePlay}
                    className="inline-flex min-h-10 min-w-10 touch-manipulation items-center justify-center rounded-full bg-primary text-primary-foreground transition-colors motion-reduce:transition-none hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/50 focus-visible:ring-offset-2"
                >
                    {isPlaying ? (
                        <Pause className="size-4 fill-current" aria-hidden="true" />
                    ) : (
                        <Play className="ml-0.5 size-4 fill-current" aria-hidden="true" />
                    )}
                </button>

                <button
                    type="button"
                    aria-label={`Playback speed ${playbackRate}x. Activate to change speed.`}
                    onClick={toggleSpeed}
                    className="inline-flex min-h-9 min-w-12 touch-manipulation items-center justify-center rounded-lg px-2 text-sm font-medium tabular-nums text-muted-foreground transition-colors motion-reduce:transition-none hover:bg-accent hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/50"
                >
                    {playbackRate}x
                </button>
            </div>
        </div>
    );
}
