import { useEffect, useState } from "react";

import type { HealthInfo } from "../types";

/** 启动时拉一次 /api/health；version 变化时重拉（设置保存后刷新降级状态）。 */
export function useHealth(version = 0) {
  const [health, setHealth] = useState<HealthInfo | null>(null);

  useEffect(() => {
    fetch("/api/health")
      .then((resp) => (resp.ok ? resp.json() : null))
      .then(setHealth)
      .catch(() => setHealth(null));
  }, [version]);

  return health;
}
