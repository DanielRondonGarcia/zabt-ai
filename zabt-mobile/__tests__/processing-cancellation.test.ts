import type {
  MeetingProcessingRun,
} from "@zabt/shared";
import {
  getCancelableProcessingRun,
  type MobileMeetingProcessingAudit,
} from "../lib/processing-cancellation";

const meeting = { id: 42, owner_id: 7, status: "processing" as const };
const owner = { id: 7 };

function run(
  id: number,
  createdAt: string,
  status: string,
): MeetingProcessingRun {
  return {
    id,
    meeting_id: meeting.id,
    owner_id: owner.id,
    trigger: "upload",
    status,
    root_task_id: null,
    created_at: createdAt,
    started_at: null,
    completed_at: null,
    final_error: null,
    events: [],
  };
}

function audit(
  currentRunId: number | null,
  ...runs: MeetingProcessingRun[]
): MobileMeetingProcessingAudit {
  return { meeting_id: meeting.id, current_run_id: currentRunId, runs };
}

describe("meeting processing cancellation selection", () => {
  it("selects the newest active exact run for the known owner", () => {
    const newestActive = run(12, "2026-10-16T10:02:00Z", "running");
    const result = getCancelableProcessingRun(
      meeting,
      audit(
        newestActive.id,
        run(13, "2026-10-16T10:03:00Z", "completed"),
        newestActive,
        run(11, "2026-10-16T10:01:00Z", "queued"),
      ),
      owner,
    );

    expect(result).toBe(newestActive);
    expect(result?.id).toBe(12);
  });

  it("fails closed unless the current user is the known meeting owner", () => {
    const activeRun = run(12, "2026-10-16T10:02:00Z", "running");
    const currentAudit = audit(activeRun.id, activeRun);

    expect(
      getCancelableProcessingRun(meeting, currentAudit, undefined),
    ).toBeNull();
    expect(
      getCancelableProcessingRun(meeting, currentAudit, { id: 8 }),
    ).toBeNull();
    expect(
      getCancelableProcessingRun(
        { ...meeting, owner_id: undefined },
        currentAudit,
        owner,
      ),
    ).toBeNull();
  });

  it.each(["completed", "failed", "cancelled"])(
    "does not select terminal run status %s",
    (status) => {
      const terminalRun = run(12, "2026-10-16T10:02:00Z", status);
      expect(
        getCancelableProcessingRun(
          meeting,
          audit(terminalRun.id, terminalRun),
          owner,
        ),
      ).toBeNull();
    },
  );

  it("keeps cancelled meetings terminal even if an audit is stale", () => {
    const activeRun = run(12, "2026-10-16T10:02:00Z", "running");
    expect(
      getCancelableProcessingRun(
        { ...meeting, status: "cancelled" },
        audit(activeRun.id, activeRun),
        owner,
      ),
    ).toBeNull();
  });

  it("rejects invalid or mismatched audit and run identities", () => {
    const activeRun = run(12, "2026-10-16T10:02:00Z", "running");

    expect(
      getCancelableProcessingRun(
        meeting,
        { ...audit(activeRun.id, activeRun), meeting_id: 43 },
        owner,
      ),
    ).toBeNull();
    expect(
      getCancelableProcessingRun(
        meeting,
        audit(99, activeRun),
        owner,
      ),
    ).toBeNull();
    expect(
      getCancelableProcessingRun(
        meeting,
        audit(activeRun.id, { ...activeRun, id: 0 }),
        owner,
      ),
    ).toBeNull();
  });
});
