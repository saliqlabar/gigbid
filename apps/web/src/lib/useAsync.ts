import { useCallback, useEffect, useState } from "react";

/** Loading / error / data state for one async call, with reload(). */
export function useAsync<T>(fn: () => Promise<{ data?: T; error?: unknown }>, deps: unknown[] = []) {
  const [data, setData] = useState<T | undefined>();
  const [error, setError] = useState<unknown>();
  const [loading, setLoading] = useState(true);
  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await fn();
      setData(r.data);
      setError(r.error);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  useEffect(() => {
    load();
  }, [load]);
  return { data, error, loading, reload: load, setData };
}
