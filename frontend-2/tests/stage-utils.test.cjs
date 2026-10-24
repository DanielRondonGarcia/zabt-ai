const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const ts = require("typescript");
const { test } = require("node:test");

function loadStages(relativePath) {
  const source = fs.readFileSync(path.resolve(__dirname, relativePath), "utf8");
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS },
  });
  const context = { exports: {} };
  vm.runInNewContext(outputText, context);
  return context.exports;
}

for (const file of ["../app/lib/stage-utils.ts", "../../zabt-mobile/lib/stage-utils.ts"]) {
  const stages = loadStages(file);
  test(`${file}: model operations have distinct ordered progress stages`, () => {
    const names = ["inferring_title", "extracting_highlights", "extracting_structured"];
    let previous = stages.STAGE_ORDER.indexOf("summarizing");
    for (const name of names) {
      assert.equal(stages.getUserStage({ status: "processing", sub_status: name }), name);
      assert.ok(stages.STAGE_LABELS[name]);
      const index = stages.STAGE_ORDER.indexOf(name);
      assert.ok(index > previous);
      previous = index;
    }
    assert.ok(previous < stages.STAGE_ORDER.indexOf("done"));
  });
  test(`${file}: existing progress and terminal outcomes remain unchanged`, () => {
    for (const [sub, expected] of [["transcribing_chunk", "transcribing"], ["summarizing", "summarizing"], ["unknown", "uploaded"]]) {
      assert.equal(stages.getUserStage({ status: "processing", sub_status: sub }), expected);
    }
    for (const status of ["failed", "cancelled", "completed"]) {
      assert.equal(stages.getUserStage({ status, sub_status: "inferring_title" }), status === "completed" ? "done" : status);
    }
  });
}

const auditStages = loadStages("../app/lib/stage-utils.ts");
test("summary audit progress labels show bounded block and synthesis positions", () => {
  assert.equal(
    auditStages.formatSummaryProgress({
      phase: "partial_chunk_summary",
      block_index: 3,
      block_total: 13,
    }),
    "Summary block 3 of 13",
  );
  assert.equal(
    auditStages.formatSummaryProgress({
      phase: "summary_synthesis",
      block_index: 14,
      block_total: 14,
    }),
    "Final synthesis (14 of 14)",
  );
  assert.equal(
    auditStages.formatSummaryProgress({
      phase: "single_chunk_summary",
      block_index: 1,
      block_total: 1,
    }),
    "Summary block 1 of 1",
  );
  assert.equal(auditStages.formatSummaryProgress({ phase: "secret-prompt", block_index: 1, block_total: 1 }), null);
  assert.equal(auditStages.formatSummaryProgress({ phase: "partial_chunk_summary", block_index: "1", block_total: 4 }), null);
  assert.equal(auditStages.formatSummaryProgress(null), null);
});

test("summary audit accepts only bounded LiteLLM trace IDs", () => {
  const traceId = "a".repeat(32);
  assert.equal(auditStages.formatSummaryCorrelationId({ correlation_id: traceId }), traceId);
  assert.equal(auditStages.formatSummaryCorrelationId({ correlation_id: "a".repeat(31) }), null);
  assert.equal(auditStages.formatSummaryCorrelationId({ correlation_id: "PROMPT_SECRET" }), null);
  assert.equal(auditStages.formatSummaryCorrelationId(null), null);
});
