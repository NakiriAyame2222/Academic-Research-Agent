import type { ResearchResult } from "../types";

interface ReflectionPanelProps {
  result: ResearchResult;
}

/** ④ 反思：研究轮次、证据缺口、待解决问题。 */
export function ReflectionPanel({ result }: ReflectionPanelProps) {
  const empty =
    result.research_rounds === 0 &&
    result.evidence_gaps.length === 0 &&
    result.open_questions.length === 0;
  if (empty) {
    return <p className="text-xs text-slate-400">无反思信息</p>;
  }
  return (
    <div className="space-y-3 text-xs">
      <p>
        研究轮次：<span className="font-mono font-medium">{result.research_rounds}</span>
      </p>
      {result.evidence_gaps.length > 0 && (
        <div>
          <p className="mb-1 font-medium text-slate-600">证据缺口</p>
          <ul className="list-disc space-y-0.5 pl-4 text-slate-600">
            {result.evidence_gaps.map((gap) => (
              <li key={gap}>{gap}</li>
            ))}
          </ul>
        </div>
      )}
      {result.open_questions.length > 0 && (
        <div>
          <p className="mb-1 font-medium text-slate-600">待解决问题</p>
          <ul className="list-disc space-y-0.5 pl-4 text-slate-600">
            {result.open_questions.map((question) => (
              <li key={question}>{question}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
