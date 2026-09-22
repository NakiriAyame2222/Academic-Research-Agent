import { useEffect, useRef, useState } from "react";

import type { JobStatus, ProgressEvent, ResearchResult } from "../types";
import { getJob } from "./client";

/**
 * 订阅一个 job 的 SSE 进度流。
 * - progress 帧：追加事件（按 id 去重兜底，防止重连补发重复渲染）。
 * - done 帧：关闭连接，再拉一次 GET /api/jobs/{id} 取完整 result（大结果不塞进 SSE）。
 * - error 帧：服务端任务失败；无 data 的 error 是连接波动，交给 EventSource 自动重连。
 */
export function useJobStream(jobId: string | null) {
  const [events, setEvents] = useState<ProgressEvent[]>([]);
  const [status, setStatus] = useState<JobStatus>("pending");
  const [result, setResult] = useState<ResearchResult | null>(null);
  const [error, setError] = useState<string>("");
  const seen = useRef<Set<number>>(new Set());

  useEffect(() => {
    if (!jobId) return;

    setEvents([]);
    setResult(null);
    setError("");
    setStatus("running");
    seen.current = new Set();

    const source = new EventSource(`/api/jobs/${jobId}/events`);

    source.addEventListener("progress", (event) => {
      const data = JSON.parse((event as MessageEvent).data) as ProgressEvent;
      if (seen.current.has(data.id)) return;
      seen.current.add(data.id);
      setEvents((prev) => [...prev, data]);
    });

    source.addEventListener("done", async () => {
      source.close();
      try {
        const view = await getJob(jobId);
        setResult(view.result);
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
        // 服务端 error 帧
        source.close();
        try {
          setError((JSON.parse(data) as { message?: string }).message || "任务失败");
        } catch {
          setError("任务失败");
        }
        setStatus("failed");
      }
      // 无 data：连接波动，EventSource 会自动重连并带 Last-Event-ID 续流
    });

    return () => source.close();
  }, [jobId]);

  return { events, status, result, error };
}
