import { useCallback, useEffect, useRef } from 'react';

export function usePolling(callback, intervalMs, enabled = true) {
  const savedCallback = useRef(callback);
  const timerRef = useRef(null);

  useEffect(() => {
    savedCallback.current = callback;
  }, [callback]);

  const stop = useCallback(() => {
    if (timerRef.current !== null) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  useEffect(() => {
    if (!enabled || !intervalMs || intervalMs <= 0) {
      stop();
      return undefined;
    }

    timerRef.current = setInterval(() => {
      savedCallback.current();
    }, intervalMs);

    return stop;
  }, [enabled, intervalMs, stop]);

  return { stop };
}
