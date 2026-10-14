import { useEffect, useRef, useState } from "react";

import type { JobStatus, ProgressEvent, ResearchResult, RunPhase, SubtopicView } from "../types";
import { getJob } from "./client";

/**
 * 订阅一个 job 的 SSE 进度流。
 * - 初始先拉取一次 GET /api/jobs/{id} 预填 topics / phase / 历史 events。
 * - progress 帧：追加事件，实时更新 topic 状态与阶段。
 * - done 帧：关闭连接，拉取最终 result。
 * - error / cancelled 帧：更新终止状态。
 */
export function useJobStream(jobId: string | null) {
  const [events, setEvents] = useState<ProgressEvent[]>([]);
  const [status, setStatus] = useState<JobStatus>("pending");
  const [phase, setPhase] = useState<RunPhase | undefined>();
  const [topics, setTopics] = useState<SubtopicView[]>([]);
  const [clarificationOptions, setClarificationOptions] = useState<Array<{ id: string; name: string }>>([]);
  const [result, setResult] = useState<ResearchResult | null>(null);
  const [error, setError] = useState<string>("");
  const seen = useRef<Set<number>>(new Set());

  useEffect(() => {
    if (!jobId) return;

    setEvents([]);
    setResult(null);
    setError("");
    setStatus("running");
    setTopics([]);
    setPhase(undefined);
    setClarificationOptions([]);
    seen.current = new Set();

    let cancelled = false;

    // 预填已知状态（重连/刷新恢复）
    getJob(jobId)
      .then((view) => {
        if (cancelled) return;
        if (view.status && view.status !== "running") {
          setStatus((prev) =>
            prev === "succeeded" || prev === "cancelled" || prev === "failed" ? prev : view.status
          );
        }
        if (view.phase) setPhase((prev) => prev ?? view.phase);
        if (view.topics && view.topics.length > 0) {
          setTopics((prev) => {
            const map = new Map<string, SubtopicView>();
            view.topics?.forEach((t) => map.set(t.id, t));
            prev.forEach((t) => map.set(t.id, { ...map.get(t.id), ...t }));
            return Array.from(map.values());
          });
        }
        if (view.events && view.events.length > 0) {
          setEvents((prev) => {
            const combined = [...prev];
            for (const ev of view.events) {
              if (!seen.current.has(ev.id)) {
                seen.current.add(ev.id);
                combined.push(ev);
              }
            }
            return combined.sort((a, b) => a.id - b.id);
          });
        }
        if (view.result) setResult(view.result);
      })
      .catch(() => undefined);

    const source = new EventSource(`/api/jobs/${jobId}/events`);

    source.addEventListener("progress", (event) => {
      const data = JSON.parse((event as MessageEvent).data) as ProgressEvent;
      if (seen.current.has(data.id)) return;
      seen.current.add(data.id);
      setEvents((prev) => [...prev, data]);

      if (data.phase) {
        setPhase(data.phase);
        if (["writing", "succeeded", "failed", "cancelled"].includes(data.phase)) {
          setClarificationOptions([]);
        }
      }
      if (data.options) setClarificationOptions(data.options);

      if (data.topic_id) {
        setTopics((prev) => {
          const idx = prev.findIndex((t) => t.id === data.topic_id);
          if (idx >= 0) {
            const current = prev[idx];
            const isTerminal = current.status === "cancelled" || current.status === "completed";
            const newStatus =
              isTerminal && (data.topic_status === "running" || data.topic_status === "queued")
                ? current.status
                : data.topic_status || current.status;
            const next = [...prev];
            next[idx] = {
              ...current,
              name: data.topic_name || current.name,
              status: newStatus,
              latest_progress: data.message || current.latest_progress,
            };
            return next;
          }
          return [
            ...prev,
            {
              id: data.topic_id!,
              name: data.topic_name || data.topic_id!,
              status: data.topic_status || "queued",
              latest_progress: data.message,
            },
          ];
        });
      }
    });

    source.addEventListener("done", async () => {
      source.close();
      try {
        const view = await getJob(jobId);
        setResult(view.result);
        if (view.topics) setTopics(view.topics);
        if (view.phase) setPhase(view.phase);
        setStatus("succeeded");
      } catch (err) {
        setError(String(err));
        setStatus("failed");
      }
    });

    source.addEventListener("cancelled", () => {
      source.close();
      setStatus("cancelled");
    });

    source.addEventListener("error", (event) => {
      const data = (event as MessageEvent).data;
      if (data) {
        source.close();
        try {
          setError((JSON.parse(data) as { message?: string }).message || "任务失败");
        } catch {
          setError("任务失败");
        }
        setStatus("failed");
      } else {
        if (source.readyState === EventSource.CLOSED) {
          source.close();
          setError("与服务端的实时流连接已中断");
          setStatus("failed");
        }
      }
    });

    return () => {
      cancelled = true;
      source.close();
    };
  }, [jobId]);

  return { events, status, phase, topics, clarificationOptions, result, error };
}
