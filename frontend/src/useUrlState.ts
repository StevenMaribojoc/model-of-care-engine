import { useCallback, useEffect, useState } from "react";

/**
 * Keeps view state in the URL query string.
 *
 * Worth the twenty lines: a worklist URL becomes shareable ("here are the 43
 * patients who need endocrinology"), the back button works, and the two role
 * views have distinct addresses rather than being a hidden toggle. Hand-rolled
 * rather than pulling in a router, since this is the only routing the app has.
 */
export function useUrlState<T extends Record<string, string>>(defaults: T) {
  const read = useCallback((): T => {
    const params = new URLSearchParams(window.location.search);
    const next = { ...defaults };
    for (const key of Object.keys(defaults) as (keyof T)[]) {
      const value = params.get(key as string);
      if (value !== null) next[key] = value as T[keyof T];
    }
    return next;
  }, [defaults]);

  const [state, setState] = useState<T>(read);

  // Back/forward should move between views, not reload the page.
  useEffect(() => {
    const onPop = () => setState(read());
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, [read]);

  const update = useCallback(
    (patch: Partial<T>) => {
      setState((current) => {
        const next = { ...current, ...patch };
        const params = new URLSearchParams();
        for (const [key, value] of Object.entries(next)) {
          // Omit defaults so the URL stays readable instead of listing every
          // filter that is not set.
          if (value && value !== defaults[key as keyof T]) params.set(key, value);
        }
        const query = params.toString();
        window.history.pushState({}, "", query ? `?${query}` : window.location.pathname);
        return next;
      });
    },
    [defaults],
  );

  return [state, update] as const;
}
