import { useCallback, useState } from 'react';

/**
 * Persisted UI settings.
 *
 * Convention (matches i18n / export-dir / active-build keys): write under the
 * `akasha:` prefix. Every read is validated against a whitelist and falls
 * back to a safe default, so a hand-edited or stale localStorage value can
 * never break the UI.
 */
const PREFIX = 'akasha:';

// `0` means "show all collections on one page" (no pagination).
export const COLLECTIONS_PER_PAGE_OPTIONS = [5, 8, 10, 15, 20, 0] as const;
export const VIDEOS_PER_PAGE_OPTIONS = [10, 20, 50, 100] as const;
export const ACTIVITY_BAR_POSITIONS = ['left', 'top', 'right', 'bottom'] as const;
export type ActivityBarPosition = (typeof ACTIVITY_BAR_POSITIONS)[number];

export const DEFAULT_COLLECTIONS_PER_PAGE = 8;
export const DEFAULT_VIDEOS_PER_PAGE = 20;
export const DEFAULT_ACTIVITY_BAR_POSITION: ActivityBarPosition = 'left';

export function readSetting<T extends string | number>(
  key: string,
  allowed: readonly T[],
  fallback: T,
): T {
  try {
    const raw = localStorage.getItem(PREFIX + key);
    if (raw == null) return fallback;
    const parsed = (typeof fallback === 'number' ? Number(raw) : raw) as T;
    return allowed.includes(parsed) ? parsed : fallback;
  } catch {
    return fallback;
  }
}

export function writeSetting<T extends string | number>(key: string, value: T): void {
  try {
    localStorage.setItem(PREFIX + key, String(value));
  } catch {
    /* ignore */
  }
}

/**
 * A whitelisted, persisted setting. Same-tab propagation is via React state;
 * we deliberately do not rely on the `storage` event (it does not fire in the
 * tab that made the change).
 */
export function useLocalStorageSetting<T extends string | number>(
  key: string,
  allowed: readonly T[],
  fallback: T,
): [T, (value: T) => void] {
  const [value, setValue] = useState<T>(() => readSetting(key, allowed, fallback));

  const set = useCallback(
    (next: T) => {
      const safe = allowed.includes(next) ? next : fallback;
      setValue(safe);
      writeSetting(key, safe);
    },
    [key, allowed, fallback],
  );

  return [value, set];
}
