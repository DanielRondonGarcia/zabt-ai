// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2025-2026 Afeef Janjua
import { useRef, useState } from "react";
import axios from "axios";
import { useQueryClient } from "@tanstack/react-query";
import { ActivityIndicator, Alert, Pressable, Text, View } from "react-native";
import type { Meeting } from "@zabt/shared";
import { getCancelableProcessingRun } from "@/lib/processing-cancellation";
import {
  cancelMeetingProcessingRun,
  useCurrentUser,
  useMeetingProcessingAudit,
} from "@/lib/queries";
import {
  STAGE_LABELS,
  STAGE_ORDER,
  VISUAL_OUTCOME_COPY,
  getUserStage,
  getVisualOutcome,
  isActiveMeeting,
} from "@/lib/stage-utils";

interface Props {
  meeting: Meeting;
}

export function StatusBanner({ meeting }: Props) {
  return (
    <>
      <MeetingStatusBanner meeting={meeting} />
      <CancelProcessingAction meeting={meeting} />
    </>
  );
}

function MeetingStatusBanner({ meeting }: Props) {
  const stage = getUserStage(meeting);
  const visualOutcome = getVisualOutcome(meeting);
  const active = isActiveMeeting(meeting);

  if (stage === "cancelled") {
    return (
      <View className="mx-4 mt-4 mb-2 px-4 py-3 rounded-lg bg-stone-50 border border-stone-200">
        <Text className="text-sm font-semibold text-stone-700">Cancelled</Text>
        <Text className="text-xs text-stone-600 mt-1">
          This meeting was cancelled and will not continue processing.
        </Text>
      </View>
    );
  }

  if (
    meeting.status === "completed" &&
    (visualOutcome === "cancelled" || visualOutcome === "error")
  ) {
    const copy = VISUAL_OUTCOME_COPY[visualOutcome];
    const failed = visualOutcome === "error";
    return (
      <View className={`mx-4 mt-4 mb-2 rounded-lg border px-4 py-3 ${failed ? "border-red-200 bg-red-50" : "border-stone-200 bg-stone-50"}`}>
        <Text className={`text-sm font-semibold ${failed ? "text-red-700" : "text-stone-700"}`}>
          {copy.title}
        </Text>
        <Text className={`mt-1 text-xs ${failed ? "text-red-600" : "text-stone-600"}`}>
          {copy.description}
        </Text>
      </View>
    );
  }

  if (stage === "visual_skipped" || stage === "visual_fallback") {
    const copy =
      VISUAL_OUTCOME_COPY[stage === "visual_skipped" ? "skipped" : "fallback"];
    return (
      <View className="mx-4 mt-4 mb-2 px-4 py-3 rounded-lg bg-stone-50 border border-stone-200">
        <Text className="text-sm font-semibold text-stone-700">{copy.title}</Text>
        <Text className="text-xs text-stone-600 mt-1">{copy.description}</Text>
      </View>
    );
  }

  if (stage === "done") return null;

  if (stage === "failed") {
    return (
      <View className="mx-4 mt-4 mb-2 px-4 py-3 rounded-lg bg-red-50 border border-red-200">
        <Text className="text-sm font-medium text-red-700">
          Processing failed
        </Text>
        <Text className="text-xs text-red-600 mt-1">
          Something went wrong on the server. Try uploading again.
        </Text>
      </View>
    );
  }

  const currentIdx = STAGE_ORDER.indexOf(stage);
  const totalSteps = STAGE_ORDER.length - 1; // exclude "done" from progress dots

  return (
    <View className="mx-4 mt-4 mb-2 px-4 py-3 rounded-lg bg-amber-50 border border-amber-200">
      <View className="flex-row items-center gap-2">
        {active && <ActivityIndicator size="small" color="#d97706" />}
        <Text className="text-sm font-semibold text-amber-700">
          {STAGE_LABELS[stage]}
        </Text>
      </View>

      <View className="flex-row gap-1.5 mt-3">
        {STAGE_ORDER.slice(0, totalSteps).map((s, i) => (
          <View
            key={s}
            className={
              i <= currentIdx ? "flex-1 h-1 rounded-full bg-amber-500" : "flex-1 h-1 rounded-full bg-amber-200"
            }
          />
        ))}
      </View>

      <Text className="text-xs text-amber-700 mt-2">
        Step {Math.min(currentIdx + 1, totalSteps)} of {totalSteps} · this updates automatically
      </Text>
    </View>
  );
}

function CancelProcessingAction({ meeting }: Props) {
  const { data: currentUser } = useCurrentUser();
  const meetingId =
    Number.isSafeInteger(meeting.id) && meeting.id > 0 ? meeting.id : undefined;
  const { data: audit } = useMeetingProcessingAudit(meetingId);
  const queryClient = useQueryClient();
  const [isSubmitting, setIsSubmitting] = useState(false);
  const isSubmittingRef = useRef(false);
  const run = getCancelableProcessingRun(meeting, audit, currentUser);

  const refreshMeetingAndAudit = async () => {
    await Promise.all([
      queryClient.invalidateQueries({
        predicate: (query) =>
          query.queryKey[0] === "meeting" &&
          query.queryKey[1] != null &&
          String(query.queryKey[1]) === String(meeting.id),
      }),
      queryClient.invalidateQueries({
        queryKey: ["meeting-processing-audit", meetingId],
      }),
    ]);
  };

  const submitCancellation = async (runId: number) => {
    if (isSubmittingRef.current) return;
    isSubmittingRef.current = true;
    setIsSubmitting(true);

    try {
      let succeeded = false;
      let failure: unknown;
      try {
        await cancelMeetingProcessingRun(meeting.id, runId);
        succeeded = true;
      } catch (error) {
        failure = error;
      }

      await refreshMeetingAndAudit().catch(() => undefined);

      if (succeeded) {
        Alert.alert(
          "Cancellation recorded",
          "Cancellation was recorded. Committed work may remain, and an in-flight provider call may finish.",
        );
      } else if (
        axios.isAxiosError(failure) &&
        failure.response?.status === 409
      ) {
        Alert.alert(
          "Processing run changed",
          "This run is no longer current or active. No cancellation was retried. Check the meeting and processing audit for the current run before trying again.",
        );
      } else {
        Alert.alert(
          "Cancellation could not be confirmed",
          "We couldn't confirm whether this run was cancelled. Check the meeting status before trying again.",
        );
      }
    } finally {
      isSubmittingRef.current = false;
      setIsSubmitting(false);
    }
  };

  if (!run) return null;

  const confirmCancellation = () => {
    Alert.alert(
      "Cancel processing?",
      "Later writes from this run will be rejected. Committed work may remain, and an in-flight provider call may finish. Cancellation does not roll back committed work or force-stop that call.",
      [
        { text: "Keep processing", style: "cancel" },
        {
          text: "Cancel processing",
          style: "destructive",
          onPress: () => void submitCancellation(run.id),
        },
      ],
    );
  };

  return (
    <View className="mx-4 mb-3">
      <Pressable
        accessibilityRole="button"
        accessibilityState={{ disabled: isSubmitting }}
        className={`items-center rounded-lg border border-red-200 bg-red-50 px-4 py-3 ${isSubmitting ? "opacity-50" : ""}`}
        disabled={isSubmitting}
        onPress={confirmCancellation}
      >
        <Text className="text-sm font-semibold text-red-700">
          {isSubmitting ? "Cancelling…" : "Cancel processing"}
        </Text>
      </Pressable>
    </View>
  );
}
