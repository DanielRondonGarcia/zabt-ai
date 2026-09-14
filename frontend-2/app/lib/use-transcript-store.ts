// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2025-2026 Afeef Janjua
import { create } from "zustand";

interface TranscriptState {
    mediaKey: string | null;
    currentTime: number;
    duration: number;
    isPlaying: boolean;
    setCurrentTime: (time: number) => void;
    setDuration: (duration: number) => void;
    setIsPlaying: (playing: boolean) => void;
    seekRequest: number | null;
    setSeekRequest: (time: number | null) => void;
    reset: (mediaKey?: string | null) => void;
}

export const useTranscriptStore = create<TranscriptState>((set) => ({
    mediaKey: null,
    currentTime: 0,
    duration: 0,
    isPlaying: false,
    setCurrentTime: (time) => set({ currentTime: time }),
    setDuration: (duration) => set({ duration }),
    setIsPlaying: (playing) => set({ isPlaying: playing }),
    seekRequest: null,
    setSeekRequest: (time) => set({ seekRequest: time }),
    reset: (mediaKey = null) => set({
        mediaKey,
        currentTime: 0,
        duration: 0,
        isPlaying: false,
        seekRequest: null,
    }),
}));
