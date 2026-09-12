import { useState, useRef, useCallback, useEffect } from 'react';
import SourcesPanel from './SourcesPanel';
import { useI18n } from '../i18n';
import { useWorkspaceStore } from '../store/workspace';

interface Props {
  onBuildDone: () => void;
  statsRefreshKey: number;
  collectionsPerPage: number;
  videosPerPage: number;
  onOpenSettings: () => void;
}

/**
 * The "collections & ingestion" workspace tab: the SourcesPanel sidebar plus a
 * main board area. PR-B keeps the board deliberately light (an overview + a
 * jump-to-chat affordance); PR-C fills it out.
 */
export default function SourcesStudio({
  onBuildDone,
  statsRefreshKey,
  collectionsPerPage,
  videosPerPage,
  onOpenSettings,
}: Props) {
  const { t } = useI18n();
  const selectedCollectionId = useWorkspaceStore(s => s.selectedCollectionId);
  const setSelectedCollectionId = useWorkspaceStore(s => s.setSelectedCollectionId);
  const setActiveTab = useWorkspaceStore(s => s.setActiveTab);

  const [leftWidth, setLeftWidth] = useState(340);
  const [isDragging, setIsDragging] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    setIsDragging(true);
  }, []);

  useEffect(() => {
    if (!isDragging) return;
    const onMove = (e: MouseEvent) => {
      const left = containerRef.current?.getBoundingClientRect().left || 0;
      setLeftWidth(Math.max(260, Math.min(560, e.clientX - left)));
    };
    const onUp = () => setIsDragging(false);
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
    return () => {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };
  }, [isDragging]);

  return (
    <div
      ref={containerRef}
      className="h-full flex min-h-0"
      style={{ userSelect: isDragging ? 'none' : undefined }}
    >
      <aside style={{ width: leftWidth, flexShrink: 0 }} className="h-full overflow-hidden no-print">
        <SourcesPanel
          onBuildDone={onBuildDone}
          selectedId={selectedCollectionId}
          onSelectCollection={setSelectedCollectionId}
          statsRefreshKey={statsRefreshKey}
          collectionsPerPage={collectionsPerPage}
          videosPerPage={videosPerPage}
          onOpenSettings={onOpenSettings}
        />
      </aside>

      <div
        onMouseDown={handleMouseDown}
        className={`w-3.5 -mx-1.5 flex-shrink-0 cursor-col-resize relative group flex items-center justify-center z-20 select-none no-print ${
          isDragging ? 'bg-accent/15' : ''
        }`}
      >
        <div className="w-[2px] h-full bg-[var(--color-border)] group-hover:bg-accent/60 transition-colors" />
      </div>

      <main className="flex-1 h-full min-w-0 overflow-y-auto bg-[var(--color-bg)]">
        <div className="max-w-2xl mx-auto px-8 py-14 flex flex-col items-center text-center gap-4">
          <div className="w-14 h-14 rounded-2xl bg-white/70 border border-black/5 shadow-md shadow-accent/15 flex items-center justify-center p-2">
            <img src="/akasha-mark.svg" alt="" className="w-full h-full object-contain" />
          </div>
          <h2 className="font-display text-xl font-bold text-[var(--color-ink)]">{t('studioBoardTitle')}</h2>
          <p className="text-sm text-[var(--color-ink-muted)] leading-6">{t('studioBoardDesc')}</p>
          <button
            type="button"
            onClick={() => setActiveTab('chat')}
            className="mt-2 px-4 py-2 rounded-xl bg-accent text-white text-sm font-semibold shadow-sm hover:shadow-md transition-all cursor-pointer"
          >
            ✨ {t('studioGoToChat')}
          </button>
        </div>
      </main>
    </div>
  );
}
