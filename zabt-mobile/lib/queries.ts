// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2025-2026 Afeef Janjua
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import type { Meeting, MeetingList, User } from "@zabt/shared";
import { api } from "./api";
import type { MobileMeetingProcessingAudit } from "./processing-cancellation";
import { isActiveMeeting } from "./stage-utils";

const POLL_MS = 5000;
export const MEETINGS_PAGE_SIZE = 10;

function isActive(m: Meeting): boolean {
  return isActiveMeeting(m);
}

function pageItems(data: MeetingList | Meeting[]): Meeting[] {
  return Array.isArray(data) ? data : data.items;
}

export function useMeetings() {
  return useInfiniteQuery({
    queryKey: ["meetings"],
    initialPageParam: 0,
    queryFn: async ({ pageParam }) => {
      const response = await api.get<MeetingList | Meeting[]>(
        `/meetings/?skip=${pageParam}&limit=${MEETINGS_PAGE_SIZE}`
      );
      return pageItems(response.data);
    },
    getNextPageParam: (lastPage, allPages) =>
      lastPage.length < MEETINGS_PAGE_SIZE
        ? undefined
        : allPages.reduce((sum, p) => sum + p.length, 0),
    // Only poll the FIRST page while any visible meeting is active. Refetching
    // every page on each poll would be wasteful and disrupt scroll position.
    refetchInterval: (query) => {
      const all = query.state.data?.pages.flat() ?? [];
      return all.some(isActive) ? POLL_MS : false;
    },
  });
}

export function useMeeting(id: number | string | undefined) {
  return useQuery<Meeting>({
    queryKey: ["meeting", id],
    queryFn: async () => {
      const response = await api.get<Meeting>(`/meetings/${id}`);
      return response.data;
    },
    enabled: id !== undefined && id !== null && id !== "",
    refetchInterval: (query) =>
      query.state.data && isActive(query.state.data) ? POLL_MS : false,
  });
}

export function useMeetingProcessingAudit(meetingId: number | undefined) {
  return useQuery<MobileMeetingProcessingAudit>({
    queryKey: ["meeting-processing-audit", meetingId],
    queryFn: async () => {
      if (
        typeof meetingId !== "number" ||
        !Number.isSafeInteger(meetingId) ||
        meetingId <= 0
      ) {
        throw new Error("A valid meeting ID is required.");
      }
      const response = await api.get<MobileMeetingProcessingAudit>(
        `/meetings/${meetingId}/processing-audit`,
      );
      return response.data;
    },
    enabled:
      typeof meetingId === "number" &&
      Number.isSafeInteger(meetingId) &&
      meetingId > 0,
    refetchInterval: (query) => {
      const runs = query.state.data?.runs;
      return Array.isArray(runs) &&
        runs.some(
          (run) => run?.status === "queued" || run?.status === "running",
        )
        ? POLL_MS
        : false;
    },
  });
}

export async function cancelMeetingProcessingRun(
  meetingId: number,
  runId: number,
): Promise<void> {
  await api.post(`/meetings/${meetingId}/processing-runs/${runId}/cancel`);
}

export function useCurrentUser() {
  return useQuery<User>({
    queryKey: ["me"],
    queryFn: async () => {
      const response = await api.get<User>("/users/me");
      return response.data;
    },
  });
}
