import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";
import vm from "node:vm";
import ts from "typescript";

const source = new URL("../src/components/discussion.ts", import.meta.url);
const code = ts.transpileModule(readFileSync(source, "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS },
}).outputText;
const exports = {};
vm.runInNewContext(code, { exports, require: createRequire(source) });
const getDiscussionIndicator = exports.getDiscussionIndicator;

const result = (metadata = {}) => ({
  session_id: "test",
  intent_mode: "topic_research",
  final_answer: "answer",
  survey_artifact: {},
  citations: [],
  citation_audit: null,
  tool_trace: [],
  tool_loop_degraded: false,
  evidence_status: [],
  evidence_gaps: [],
  open_questions: [],
  research_rounds: 1,
  selected_papers: [],
  paper_notes: [],
  pending_memory_candidates: [],
  report_download_url: null,
  discussion: metadata,
});

test("discussion indicator stays absent for results without metadata", () => {
  assert.equal(getDiscussionIndicator(result()), null);
});

test("discussion metadata exposes scope and normalized sources", () => {
  assert.equal(JSON.stringify(getDiscussionIndicator(result({
    scope: "当前报告",
    report_used: true,
    selected_paper_notes_used: true,
    on_demand_evidence_used: true,
  }))), JSON.stringify({
    scope: "当前报告",
    sources: ["研究报告", "已选论文笔记", "按需证据"],
  }));
});
