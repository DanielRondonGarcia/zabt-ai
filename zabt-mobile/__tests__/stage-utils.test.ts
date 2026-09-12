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

  it("provides copy for both non-fatal visual outcomes", () => {
    expect(VISUAL_OUTCOME_COPY.skipped.description).toContain("transcript-only");
    expect(VISUAL_OUTCOME_COPY.fallback.description).toContain("transcript-only");
  });
});
