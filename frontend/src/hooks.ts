import { useCallback, useEffect, useRef, useState } from "react";

/** Данные с бэкенда с опросом каждые `ms`; reload() — обновить сразу. */
export function usePoll<T>(load: () => Promise<T>, ms: number, deps: unknown[]) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const loadRef = useRef(load);
  loadRef.current = load;
  const reload = useCallback(async () => {
    try {
      setData(await loadRef.current());
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);
  useEffect(() => {
    setData(null);
    reload();
    const t = setInterval(reload, ms);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return { data, error, reload };
}

/** Ширина элемента в пикселях — графики рисуются в настоящем размере, без сплющивания. */
export function useWidth<E extends HTMLElement>(fallback = 600) {
  const ref = useRef<E>(null);
  const [width, setWidth] = useState(fallback);
  useEffect(() => {
    if (!ref.current) return;
    const ro = new ResizeObserver(([e]) => setWidth(Math.max(260, Math.round(e.contentRect.width))));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);
  return [ref, width] as const;
}

export function useStored<T extends string>(key: string, initial: T) {
  const [value, setValue] = useState<T>(() => {
    try { return (localStorage.getItem(key) as T) || initial; } catch { return initial; }
  });
  const set = (v: T) => {
    setValue(v);
    try { localStorage.setItem(key, v); } catch { /* приватный режим */ }
  };
  return [value, set] as const;
}
