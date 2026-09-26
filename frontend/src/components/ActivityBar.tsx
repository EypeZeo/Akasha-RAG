import { useRef, useState, useEffect } from 'react';
import { useI18n, SupportedLang } from '../i18n';
import type { ActivityBarPosition } from '../utils/settings';
import type { WorkspaceTab } from '../store/workspace';
import * as api from '../api';

interface Props {
  position: ActivityBarPosition;
  activeTab: WorkspaceTab;
  onTabChange: (tab: WorkspaceTab) => void;
  onOpenSettings: () => void;
  loggedPlatforms: api.PlatformInfo[];
}

interface TabDef {
  id: WorkspaceTab;
  icon: string;
  labelKey: string;
}

const TABS: TabDef[] = [
  { id: 'sources', icon: '📚', labelKey: 'tabSources' },
  { id: 'chat', icon: '✨', labelKey: 'tabChat' },
];

export default function ActivityBar({
  position,
  activeTab,
  onTabChange,
  onOpenSettings,
  loggedPlatforms,
}: Props) {
  const { t, lang, setLang, languages } = useI18n();
  const isVertical = position === 'left' || position === 'right';
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const [langMenuOpen, setLangMenuOpen] = useState(false);
  const langWrapRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!langMenuOpen) return;
    const onDoc = (e: MouseEvent) => {
      if (langWrapRef.current && !langWrapRef.current.contains(e.target as Node)) {
        setLangMenuOpen(false);
      }
    };
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, [langMenuOpen]);

  const onTabKeyDown = (e: React.KeyboardEvent, index: number) => {
    const nextKey = isVertical ? 'ArrowDown' : 'ArrowRight';
    const prevKey = isVertical ? 'ArrowUp' : 'ArrowLeft';
    let target = -1;
    if (e.key === nextKey) target = (index + 1) % TABS.length;
    else if (e.key === prevKey) target = (index - 1 + TABS.length) % TABS.length;
    else if (e.key === 'Home') target = 0;
    else if (e.key === 'End') target = TABS.length - 1;
    if (target >= 0) {
      e.preventDefault();
      onTabChange(TABS[target].id);
      tabRefs.current[target]?.focus();
    }
  };

  const barClass = isVertical
    ? 'flex flex-col items-center w-[52px] py-2 gap-1'
    : 'flex flex-row items-center h-12 px-2 gap-1';
  const edgeBorder = {
    left: 'border-r',
    right: 'border-l',
    top: 'border-b',
    bottom: 'border-t',
  }[position];

  const toolsWrapClass = isVertical ? 'mt-auto flex flex-col items-center gap-1' : 'ml-auto flex flex-row items-center gap-1';

  return (
    <div
      className={`${barClass} ${edgeBorder} border-[var(--color-border)] bg-[var(--color-panel)] flex-shrink-0 no-print select-none`}
    >
      <div
        role="tablist"
        aria-label={t('activityBarLabel')}
        aria-orientation={isVertical ? 'vertical' : 'horizontal'}
        className={isVertical ? 'flex flex-col gap-1' : 'flex flex-row gap-1'}
      >
        {TABS.map((tab, i) => {
          const selected = activeTab === tab.id;
          return (
            <button
              key={tab.id}
              ref={el => { tabRefs.current[i] = el; }}
              role="tab"
              aria-selected={selected}
              aria-controls={`workspace-pane-${tab.id}`}
              tabIndex={selected ? 0 : -1}
              onClick={() => onTabChange(tab.id)}
              onKeyDown={e => onTabKeyDown(e, i)}
              title={t(tab.labelKey)}
              className={`relative w-9 h-9 rounded-xl flex items-center justify-center text-lg transition-all cursor-pointer ${
                selected
                  ? 'bg-accent-light text-accent shadow-2xs'
                  : 'text-[var(--color-ink-muted)] hover:bg-black/5 hover:text-[var(--color-ink)]'
              }`}
            >
              <span aria-hidden>{tab.icon}</span>
              {selected && (
                <span
                  aria-hidden
                  className={`absolute rounded-full bg-accent ${
                    position === 'left' ? 'left-0 top-1.5 bottom-1.5 w-[3px]'
                      : position === 'right' ? 'right-0 top-1.5 bottom-1.5 w-[3px]'
                      : position === 'top' ? 'top-0 left-1.5 right-1.5 h-[3px]'
                      : 'bottom-0 left-1.5 right-1.5 h-[3px]'
                  }`}
                />
              )}
            </button>
          );
        })}
      </div>

      <div className={toolsWrapClass}>
        {/* Platform login status */}
        <button
          type="button"
          onClick={onOpenSettings}
          title={loggedPlatforms.length ? `${t('loggedIn')} ${loggedPlatforms.length}/2` : t('notLoggedIn')}
          className="w-9 h-9 rounded-xl flex items-center justify-center hover:bg-black/5 transition-all cursor-pointer relative"
        >
          <span
            className={`w-2.5 h-2.5 rounded-full ${
              loggedPlatforms.length ? 'bg-[var(--color-success)] animate-pulse' : 'bg-gray-400'
            }`}
          />
        </button>

        {/* Language quick switch */}
        <div ref={langWrapRef} className="relative">
          <button
            type="button"
            onClick={() => setLangMenuOpen(o => !o)}
            aria-haspopup="menu"
            aria-expanded={langMenuOpen}
            title={t('languageTitle')}
            className="w-9 h-9 rounded-xl flex items-center justify-center text-base text-[var(--color-ink-muted)] hover:bg-black/5 hover:text-[var(--color-ink)] transition-all cursor-pointer"
          >
            🌐
          </button>
          {langMenuOpen && (
            <div
              role="menu"
              className={`absolute z-50 min-w-[150px] max-h-[min(70vh,24rem)] overflow-y-auto rounded-xl border border-[var(--color-border)] bg-white shadow-xl p-1 ${
                isVertical
                  ? (position === 'left' ? 'left-full bottom-0 ml-1' : 'right-full bottom-0 mr-1')
                  : (position === 'top' ? 'right-0 top-full mt-1' : 'right-0 bottom-full mb-1')
              }`}
            >
              {languages.map(item => (
                <button
                  key={item.code}
                  role="menuitemradio"
                  aria-checked={lang === item.code}
                  onClick={() => { setLang(item.code as SupportedLang); setLangMenuOpen(false); }}
                  className={`w-full text-left px-2.5 py-1.5 rounded-lg text-xs transition-colors cursor-pointer ${
                    lang === item.code
                      ? 'bg-accent-light text-accent font-semibold'
                      : 'text-[var(--color-ink-soft)] hover:bg-black/5'
                  }`}
                >
                  {item.label}
                  <span className="text-[10px] text-[var(--color-ink-muted)] ml-1.5">{item.name}</span>
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Settings */}
        <button
          type="button"
          onClick={onOpenSettings}
          title={t('settingsTitle')}
          className="w-9 h-9 rounded-xl flex items-center justify-center text-[var(--color-ink-muted)] hover:bg-black/5 hover:text-accent transition-all cursor-pointer"
        >
          <svg className="w-[18px] h-[18px]" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
            <path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z" />
            <circle cx="12" cy="12" r="3" />
          </svg>
        </button>
      </div>
    </div>
  );
}
