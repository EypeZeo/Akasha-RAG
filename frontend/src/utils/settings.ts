import { useCallback, useEffect, useState } from 'react';

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
export const COLLECTION_EXPAND_MODES = ['anywhere', 'chevron'] as const;
export type CollectionExpandMode = (typeof COLLECTION_EXPAND_MODES)[number];

export const DEFAULT_COLLECTIONS_PER_PAGE = 8;
export const DEFAULT_VIDEOS_PER_PAGE = 20;
export const DEFAULT_ACTIVITY_BAR_POSITION: ActivityBarPosition = 'left';
export const DEFAULT_COLLECTION_EXPAND_MODE: CollectionExpandMode = 'anywhere';
export const STATUS_FILTER_ENABLED = [0, 1] as const;
export const DEFAULT_STATUS_FILTER_ENABLED = 1;
export const MIN_SOURCES_PANEL_WIDTH = 260;
export const MAX_SOURCES_PANEL_WIDTH = 560;
export const DEFAULT_SOURCES_PANEL_WIDTH = 340;

export const THEME_OPTIONS = ['dawn', 'midnight', 'ocean', 'forest'] as const;
export type ThemeId = (typeof THEME_OPTIONS)[number];
export const DEFAULT_THEME: ThemeId = 'dawn';

function applyTheme(theme: ThemeId): void {
  try {
    document.documentElement.dataset.theme = theme;
  } catch {
    /* rendering can be unavailable during non-browser tests */
  }
}

/** Persist the visual theme and apply it to the document root immediately. */
export function useThemeSetting(): [ThemeId, (theme: ThemeId) => void] {
  const [theme, setThemeState] = useState<ThemeId>(() => {
    const initial = readSetting('ui.theme', THEME_OPTIONS, DEFAULT_THEME);
    applyTheme(initial);
    return initial;
  });

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  const setTheme = useCallback((next: ThemeId) => {
    const safe = THEME_OPTIONS.includes(next) ? next : DEFAULT_THEME;
    if (safe === theme) return;
    setThemeState(safe);
    writeSetting('ui.theme', safe);
  }, [theme]);

  return [theme, setTheme];
}

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

/** Read the draggable Sources panel width without accepting malformed storage. */
export function readSourcesPanelWidth(): number {
  try {
    const value = Number(localStorage.getItem(PREFIX + 'ui.sourcesPanelWidth'));
    return Number.isFinite(value) && value >= MIN_SOURCES_PANEL_WIDTH && value <= MAX_SOURCES_PANEL_WIDTH
      ? Math.round(value)
      : DEFAULT_SOURCES_PANEL_WIDTH;
  } catch {
    return DEFAULT_SOURCES_PANEL_WIDTH;
  }
}

export function writeSourcesPanelWidth(width: number): void {
  if (!Number.isFinite(width)) return;
  writeSetting('ui.sourcesPanelWidth', Math.round(Math.max(MIN_SOURCES_PANEL_WIDTH, Math.min(MAX_SOURCES_PANEL_WIDTH, width))));
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
