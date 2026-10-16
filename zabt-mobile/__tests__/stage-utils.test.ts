import {
  getUserStage,
  getVisualOutcome,
  VISUAL_OUTCOME_COPY,
  isActiveMeeting,
} from "../lib/stage-utils";

describe("visual status mapping", () => {
  it.each([
    ["queued", "loading"],
    ["processing", "loading"],
    ["completed", "success"],
    ["skipped", "skipped"],
    ["fallback", "fallback"],
    ["cancelled", "cancelled"],
    ["failed", "error"],
  ] as const)("maps visual status %s to %s", (visualStatus, expected) => {
    expect(
      getVisualOutcome({
        status: "completed",
        visual_breakdown_status: visualStatus,
      })
    ).toBe(expected);
  });

  it("maps a failed meeting to the error outcome", () => {
    expect(
      getVisualOutcome({
        status: "failed",
        visual_breakdown_status: "fallback",
      })
    ).toBe("error");
  });

  it("preserves the legacy outcome when no visual status is present", () => {
    expect(
      getVisualOutcome({ status: "completed", visual_breakdown_status: null })
    ).toBeNull();
  });
});

describe("completed meeting stage mapping", () => {
  const baseMeeting = {
    status: "completed" as const,
    sub_status: null,
  };

  it.each([
    ["skipped", "visual_skipped"],
    ["fallback", "visual_fallback"],
  ] as const)("keeps visual %s visible after completion", (visualStatus, expected) => {
    expect(
      getUserStage({
        ...baseMeeting,
        visual_breakdown_status: visualStatus,
      })
    ).toBe(expected);
  });

  it("keeps a successful visual breakdown as done", () => {
    expect(
      getUserStage({
        ...baseMeeting,
        visual_breakdown_status: "completed",
      })
    ).toBe("done");
  });

  it("keeps an active visual rerun visible even after generic completion", () => {
    const meeting = {
      ...baseMeeting,
      visual_breakdown_status: "processing" as const,
    };

    expect(getUserStage(meeting)).toBe("analyzing_video");
    expect(isActiveMeeting(meeting)).toBe(true);
  });

  it.each([
    ["cancelled", "cancelled"],
    ["failed", "error"],
  ] as const)("keeps terminal visual status %s completed and inactive", (visualStatus, outcome) => {
    const meeting = {
      ...baseMeeting,
      visual_breakdown_status: visualStatus,
    };

    expect(getVisualOutcome(meeting)).toBe(outcome);
    expect(getUserStage(meeting)).toBe("done");
    expect(isActiveMeeting(meeting)).toBe(false);
  });

  it("provides copy for both non-fatal visual outcomes", () => {
    expect(VISUAL_OUTCOME_COPY.skipped.description).toContain("transcript-only");
    expect(VISUAL_OUTCOME_COPY.fallback.description).toContain("transcript-only");
  });

  it("provides distinct copy for cancelled and failed visual runs", () => {
    expect(VISUAL_OUTCOME_COPY.cancelled.title).toBe("Visual analysis cancelled");
    expect(VISUAL_OUTCOME_COPY.error.title).toBe("Visual analysis failed");
  });
});

describe("processing stage mapping", () => {
  it("shows silent-video transcription as a non-error continuation", () => {
    expect(
      getUserStage({
        status: "processing",
        sub_status: "transcription_skipped_no_audio",
        visual_breakdown_status: null,
      })
    ).toBe("transcription_skipped");
  });

  it.each([
    "preparing_audio",
    "transcribing_chunk",
  ] as const)("maps %s to the transcribing stage", (subStatus) => {
    expect(
      getUserStage({
        status: "processing",
        sub_status: subStatus,
        visual_breakdown_status: null,
      })
    ).toBe("transcribing");
  });
});

describe("cancelled meeting stage mapping", () => {
  const cancelledMeeting = {
    status: "cancelled" as const,
    sub_status: "transcribing",
    visual_breakdown_status: "processing" as const,
  };

  it("keeps cancelled meetings terminal despite stale processing state", () => {
    expect(getUserStage(cancelledMeeting)).toBe("cancelled");
    expect(getVisualOutcome(cancelledMeeting)).toBeNull();
    expect(isActiveMeeting(cancelledMeeting)).toBe(false);
  });
});
