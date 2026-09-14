// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2025-2026 Afeef Janjua
"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useTranscriptStore } from "@/app/lib/use-transcript-store";
import type { Meeting } from "@/app/lib/api";
import { Play, Pause, RotateCcw } from "lucide-react";

interface StickyMediaPlayerProps {
    meeting: Meeting;
    onHeightChange?: (height: number) => void;
}

const MEDIA_ERROR_MESSAGE = "This media could not be loaded. Transcript seeking is still available.";

export function StickyMediaPlayer({ meeting, onHeightChange }: StickyMediaPlayerProps) {
    const mediaRef = useRef<HTMLMediaElement>(null);
    const playerRef = useRef<HTMLDivElement>(null);
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
    const mediaType = meeting.media_type === "video" ? "video" : "audio";
    const mediaSrc = meeting.audio_url || meeting.file_path || undefined;
    const mediaKey = `${meeting.id}:${mediaSrc ?? ""}:${mediaType}`;
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

    useEffect(() => {
        const element = playerRef.current;
        if (!element || !onHeightChange) return;

        const reportHeight = () => onHeightChange(element.getBoundingClientRect().height);
        reportHeight();

        if (typeof ResizeObserver === "undefined") return;

        const observer = new ResizeObserver(reportHeight);
        observer.observe(element);
        return () => {
            observer.disconnect();
            onHeightChange(0);
        };
    }, [onHeightChange]);

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

        const handleLoadedMetadata = () => {
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
            setIsPlaying(false);
            stopProgressLoop();
            setMediaError(MEDIA_ERROR_MESSAGE);
        };

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
                        setIsPlaying(false);
                        setMediaError(MEDIA_ERROR_MESSAGE);
                    }
                });
            } else {
                media.pause();
            }
        }
    };

    const handleRewind = () => {
        const media = mediaRef.current;
        if (media) {
            media.currentTime = Math.max(0, media.currentTime - 10);
            setCurrentTime(media.currentTime);
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
        if (speaker === "SPEAKER_01") return "bg-amber-600";
        if (speaker === "SPEAKER_02") return "bg-teal-600";
        return "bg-stone-300";
    };

    const formatTime = (secs: number) => {
        if (!Number.isFinite(secs) || secs < 0) return "00:00";
        const m = Math.floor(secs / 60);
        const s = Math.floor(secs % 60);
        return `${m.toString().padStart(2, "0")}:${s.toString().padStart(2, "0")}`;
    };

    return (
        <div ref={playerRef} className="fixed bottom-0 left-0 right-0 bg-white border-t border-stone-200 z-50 px-6 py-4 pb-safe">
            <div className="max-w-5xl mx-auto flex flex-col gap-3">
                {mediaType === "video" ? (
                    <video
                        key={mediaKey}
                        ref={setMediaRef}
                        src={mediaSrc}
                        preload="metadata"
                        playsInline
                        aria-label={`${meeting.title} video`}
                        className="aspect-video w-full max-h-64 rounded-lg bg-stone-900 object-contain"
                    />
                ) : (
                    <audio
                        key={mediaKey}
                        ref={setMediaRef}
                        src={mediaSrc}
                        preload="metadata"
                        className="sr-only"
                        aria-hidden="true"
                    />
                )}

                {mediaError && (
                    <p role="status" aria-live="polite" className="text-sm text-amber-700">
                        {mediaError}
                    </p>
                )}

                {/* Progress Bar with Speaker Segments */}
                <div className="w-full h-1 bg-stone-100 rounded-lg relative group cursor-pointer overflow-hidden"
                    onClick={(e) => {
                        // Simple seek to click position
                        if (!mediaRef.current || duration <= 0) return;
                        const bounds = e.currentTarget.getBoundingClientRect();
                        const percent = (e.clientX - bounds.left) / bounds.width;
                        const newTime = percent * duration;
                        setSeekRequest(newTime);
                    }}
                >
                    {/* Base mapped segments */}
                    {duration > 0 && meeting.segments?.map((seg, i) => {
                        const left = (seg.start / duration) * 100;
                        const width = ((seg.end - seg.start) / duration) * 100;
                        return (
                            <div
                                key={i}
                                className={`absolute top-0 bottom-0 ${getSpeakerColor(seg.speaker)} opacity-30`}
                                style={{ left: `${left}%`, width: `${width}%` }}
                            />
                        );
                    })}

                    {/* Active progress fill */}
                    <div
                        className="absolute top-0 bottom-0 left-0 bg-primary transition-all duration-75"
                        style={{ width: `${progressPercent}%` }}
                    />
                </div>

                {/* Controls */}
                <div className="flex items-center justify-between">

                    {/* Left: Time + Main Controls */}
                    <div className="flex items-center gap-6">
                        <span className="text-xs font-medium text-stone-500 w-10 text-right font-mono">
                            {formatTime(currentTime)}
                        </span>

                        <div className="flex items-center gap-4">
                            <button type="button" aria-label="Rewind 10 seconds" onClick={handleRewind} className="hover:text-stone-900 text-stone-500 transition">
                                <RotateCcw className="w-5 h-5" />
                            </button>

                            <button type="button" aria-label={isPlaying ? "Pause" : "Play"} onClick={togglePlay} className="w-8 h-8 flex items-center justify-center rounded-full bg-stone-900 text-white hover:bg-stone-800 transition-colors">
                                {isPlaying ? (
                                    <Pause className="w-4 h-4 fill-current" />
                                ) : (
                                    <Play className="w-4 h-4 fill-current ml-0.5" />
                                )}
                            </button>

                            <button
                                type="button"
                                aria-label={`Playback speed ${playbackRate}x`}
                                onClick={toggleSpeed}
                                className="hover:text-stone-900 text-stone-500 font-medium text-sm w-6 text-center transition"
                            >
                                {playbackRate}x
                            </button>
                        </div>

                        <span className="text-xs font-medium text-stone-400 w-10 font-mono">
                            {formatTime(duration)}
                        </span>
                    </div>

                    {/* Spacer for balanced layout */}
                    <div />
                </div>

            </div>
        </div>
    );
}
