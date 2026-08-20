import { useCallback, useEffect, useRef, useState } from "react";

interface UseDataOptions<T> {
  initial?: T;
  onError?: (error: unknown) => void;
}

interface UseDataResult<T> {
  data: T | undefined;
  loading: boolean;
  error: string;
  refresh: () => Promise<void>;
  setData: React.Dispatch<React.SetStateAction<T | undefined>>;
}

export function useData<T>(fetcher: () => Promise<T>, deps: ReadonlyArray<unknown> = [], options: UseDataOptions<T> = {}): UseDataResult<T> {
  const [data, setData] = useState<T | undefined>(options.initial);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const result = await fetcherRef.current();
      setData(result);
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : "Something went wrong";
      setError(message);
      options.onError?.(reason);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps]);

  return { data, loading, error, refresh: load, setData };
}