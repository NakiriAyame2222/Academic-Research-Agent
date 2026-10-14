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
    return <p className="font-sans text-xs text-ink-muted">无反思信息</p>;
  }
  return (
    <div className="space-y-3 font-sans text-xs text-ink">
      <div className="rounded-md border border-rule bg-surface p-2.5">
        <p>
          研究轮次：<span className="font-mono font-medium text-accent">{result.research_rounds}</span>
        </p>
      </div>
      {result.evidence_gaps.length > 0 && (
        <div className="rounded-md border border-rule bg-surface p-2.5">
          <p className="mb-1.5 font-medium text-ink">证据缺口</p>
          <ul className="list-disc space-y-1 pl-4 font-serif text-xs text-ink-muted">
            {result.evidence_gaps.map((gap) => (
              <li key={gap}>{gap}</li>
            ))}
          </ul>
        </div>
      )}
      {result.open_questions.length > 0 && (
        <div className="rounded-md border border-rule bg-surface p-2.5">
          <p className="mb-1.5 font-medium text-ink">待解决问题</p>
          <ul className="list-disc space-y-1 pl-4 font-serif text-xs text-ink-muted">
            {result.open_questions.map((question) => (
              <li key={question}>{question}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
