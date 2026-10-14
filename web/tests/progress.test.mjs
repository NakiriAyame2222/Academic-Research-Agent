import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";
import vm from "node:vm";
import ts from "typescript";

const source = new URL("../src/components/ProgressLog.tsx", import.meta.url);
const code = ts.transpileModule(readFileSync(source, "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
}).outputText;
const exports = {};
vm.runInNewContext(code, { exports, require: createRequire(source) });
const format = exports.formatProgressMessage;

test("arXiv URLs and search parameters become a short label", () => {
  assert.equal(format("arXiv 重定向到 https://export.arxiv.org/api/query?search_query=all:example"), "搜索 arXiv");
  assert.equal(format("arXiv 搜索完成：long query，命中 3 篇"), "搜索 arXiv 完成 · 命中 3 篇");
});

test("retry exhaustion and capability fallback do not claim ongoing retry", () => {
  assert.match(format("LLM 网络错误，已重试 10 次仍失败，请检查网络后重新执行。"), /重试耗尽/);
  assert.equal(format("arXiv 返回 HTTP 400，跳过该查询"), "arXiv 搜索失败");
  assert.equal(format("端点不支持 tool calling，改用确定性取证流程"), "使用备用工具调用流程");
});

test("very long unbroken text has a bounded display length", () => {
  assert.ok(format("x".repeat(2000)).length <= 141);
});

test("active retry has an honest short status", () => {
  assert.equal(format("LLM 暂时不可用，60 秒后重试（1/10）"), "LLM 暂时不可用，正在重试");
});
