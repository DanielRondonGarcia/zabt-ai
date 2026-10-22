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
