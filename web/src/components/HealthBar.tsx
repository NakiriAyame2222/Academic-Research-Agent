import { useHealth } from "../api/useHealth";
import type { HealthInfo } from "../types";

interface HealthBarProps {
  health: HealthInfo | null;
}

/** 健康状态徽章条：LLM / tool calling / 向量后端 / jieba。"异常"项标黄并展开降级通知。 */
export function HealthBar({ health }: HealthBarProps) {
  if (!health) return null;

  const items: { label: string; ok: boolean; detail: string }[] = [
    { label: "LLM", ok: health.llm_available, detail: health.llm_available ? "已连接" : "演示/降级模式" },
    { label: "Tool Calling", ok: health.tool_calling, detail: health.tool_calling ? "可用" : "已降级（JSON mode）" },
    { label: `向量后端 ${health.vector_backend}`, ok: true, detail: "检索后端" },
    { label: "jieba", ok: health.jieba, detail: health.jieba ? "中文分词可用" : "未安装" },
  ];

  return (
    <div className="flex flex-wrap items-center gap-2 text-xs">
      {items.map((item) => (
        <span
          key={item.label}
          title={item.detail}
          className={`rounded-full px-2 py-0.5 ${
            item.ok ? "bg-green-100 text-green-700" : "bg-amber-100 text-amber-700"
          }`}
        >
          {item.ok ? "✓" : "⚠"} {item.label}
        </span>
      ))}
      {health.degrade_notices.map((notice) => (
        <span key={notice} className="rounded-full bg-amber-100 px-2 py-0.5 text-amber-700" title={notice}>
          ⚠ {notice.length > 40 ? `${notice.slice(0, 40)}…` : notice}
        </span>
      ))}
    </div>
  );
}

/** 顶栏用的包装：自己拉取 health。 */
export function HealthBadge() {
  const health = useHealth();
  return <HealthBar health={health} />;
}
