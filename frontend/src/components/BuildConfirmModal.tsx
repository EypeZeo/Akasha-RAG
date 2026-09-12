import { useState, useEffect, useCallback } from 'react';
import * as api from '../api';
import { useI18n } from '../i18n';

interface Props {
  onClose: () => void;
  onConfirm: (
    selectedIds: string[],
    contentType?: 'all' | 'video' | 'note',
    scope?: 'all' | 'selected',
    platform?: string,
  ) => void;
  pendingCount: number;
  initialType?: 'all' | 'video' | 'note';
  collectionId?: string | null;
  collectionTitle?: string;
  platform?: string;
}

export default function BuildConfirmModal({
  onClose,
  onConfirm,
  pendingCount,
  initialType = 'all',
  collectionId,
  collectionTitle,
  platform,
}: Props) {
  const { t } = useI18n();
  const [items, setItems] = useState<api.VideoItem[]>([]);
  const [stats, setStats] = useState<{ total: number; video_count: number; note_count: number }>({
    total: pendingCount,
    video_count: 0,
    note_count: 0,
  });
  const [page, setPage] = useState(1);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [building, setBuilding] = useState(false);
  const [tabFilter, setTabFilter] = useState<'all' | 'video' | 'note'>(initialType);

  // 'all' 表示范围入库模式（直接在数据库原子全选，零网络传输巨量ID）
  // 'custom' 表示用户显式点选勾选特定条目
  const [selectionMode, setSelectionMode] = useState<'all' | 'custom'>('all');
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());

  // 分批加载数据
  const fetchPage = useCallback(
    async (targetPage: number, append: boolean = false) => {
      if (targetPage === 1) {
        setLoading(true);
      } else {
        setLoadingMore(true);
      }

      try {
        const r = await api.listPendingKnowledge(collectionId, tabFilter, targetPage, 50, platform);
        if (r.success) {
          setStats({
            total: r.total,
            video_count: r.video_count,
            note_count: r.note_count,
          });
          setHasMore(r.has_more);
          setPage(r.page);

          if (append) {
            setItems(prev => {
              const seen = new Set(prev.map(v => v.id));
              const fresh = r.items.filter(v => !seen.has(v.id));
              return [...prev, ...fresh];
            });
          } else {
            setItems(r.items);
          }
        }
      } catch (e) {
        console.error('分批加载待入库内容失败:', e);
      } finally {
        setLoading(false);
        setLoadingMore(false);
      }
    },
    [collectionId, tabFilter, platform]
  );

  // 切换分类标签或收藏夹时重置分页并从第 1 页拉取
  useEffect(() => {
    setPage(1);
    setHasMore(false);
    fetchPage(1, false);
  }, [fetchPage]);

  // 滚动触底自动加载下一批（无限滚动）
  const handleScroll = (e: React.UIEvent<HTMLDivElement>) => {
    if (loading || loadingMore || !hasMore) return;
    const { scrollTop, scrollHeight, clientHeight } = e.currentTarget;
    if (scrollHeight - (scrollTop + clientHeight) < 80) {
      fetchPage(page + 1, true);
    }
  };

  // 当前分类标签下的真实总数
  const currentTotal = tabFilter === 'video'
    ? stats.video_count
    : (tabFilter === 'note' ? stats.note_count : stats.total);

  // 选中的数量计算
  const selectedCount = selectionMode === 'all'
    ? currentTotal
    : selectedIds.size;

  const selectedVideoCount = selectionMode === 'all'
    ? (tabFilter === 'note' ? 0 : stats.video_count)
    : items.filter(v => selectedIds.has(String(v.id)) && (v.item_type === 'video' || (v.duration ?? 0) > 0)).length;

  const selectedNoteCount = selectionMode === 'all'
    ? (tabFilter === 'video' ? 0 : stats.note_count)
    : items.filter(v => selectedIds.has(String(v.id)) && (v.item_type === 'note' || (v.duration ?? 0) === 0)).length;

  // 单项勾选与取消
  const toggleOne = (id: string) => {
    if (selectionMode === 'all') {
      const nextSet = new Set(items.map(v => String(v.id)));
      nextSet.delete(id);
      setSelectedIds(nextSet);
      setSelectionMode('custom');
    } else {
      setSelectedIds(prev => {
        const next = new Set(prev);
        if (next.has(id)) next.delete(id);
        else next.add(id);
        return next;
      });
    }
  };

  // 快捷操作
  const handleSelectAll = () => {
    setSelectionMode('all');
    setSelectedIds(new Set());
  };

  const handleSelectVideos = () => {
    setTabFilter('video');
    setSelectionMode('all');
    setSelectedIds(new Set());
  };

  const handleSelectNotes = () => {
    setTabFilter('note');
    setSelectionMode('all');
    setSelectedIds(new Set());
  };

  const handleClearSelection = () => {
    setSelectionMode('custom');
    setSelectedIds(new Set());
  };

  const handleConfirm = () => {
    setBuilding(true);
    if (selectionMode === 'all') {
      onConfirm([], tabFilter, 'all', platform);
    } else {
      let actualType: 'all' | 'video' | 'note' = 'all';
      if (selectedVideoCount > 0 && selectedNoteCount === 0) actualType = 'video';
      else if (selectedNoteCount > 0 && selectedVideoCount === 0) actualType = 'note';
      onConfirm([...selectedIds], actualType, 'selected', platform);
    }
  };

  const isItemChecked = (id: string) => {
    if (selectionMode === 'all') return true;
    return selectedIds.has(id);
  };

  const allDisplayedChecked = items.length > 0 && (
    selectionMode === 'all' || items.every(v => selectedIds.has(String(v.id)))
  );

  const hasSelection = selectedCount > 0;

  return (
    <div className="fixed inset-0 bg-black/40 backdrop-blur-xs flex items-center justify-center z-50 animate-fadeIn" onClick={onClose}>
      <div className="bg-[var(--color-panel)] rounded-2xl w-[530px] max-h-[85vh] flex flex-col shadow-2xl border border-[var(--color-border)]" onClick={e => e.stopPropagation()}>
        {/* Header */}
        <div className="px-6 pt-5 pb-3">
          <div className="flex items-center justify-between">
            <h2 className="font-display text-lg font-bold text-[var(--color-ink)] flex items-center gap-2">
              <span>🚀</span> {t('confirmBuild')}
              {collectionTitle && (
                <span className="text-xs font-normal px-2 py-0.5 rounded-full bg-accent/10 text-accent border border-accent/20">
                  {collectionTitle}
                </span>
              )}
            </h2>
            <button onClick={onClose} className="text-gray-400 hover:text-gray-600 text-sm cursor-pointer p-1">✕</button>
          </div>
          <p className="text-xs text-[var(--color-ink-muted)] mt-1">
            {t('buildPipelineDesc')}
          </p>
        </div>

        {/* 分类 Tabs */}
        <div className="flex border-b border-[var(--color-border)] px-6 gap-2">
          <button
            onClick={() => setTabFilter('all')}
            className={`pb-2 px-2 text-xs font-semibold border-b-2 transition-all cursor-pointer ${
              tabFilter === 'all'
                ? 'border-accent text-accent'
                : 'border-transparent text-[var(--color-ink-muted)] hover:text-[var(--color-ink)]'
            }`}
          >
            {t('allWithCount', { count: stats.total })}
          </button>
          <button
            onClick={() => setTabFilter('video')}
            className={`pb-2 px-2 text-xs font-semibold border-b-2 transition-all cursor-pointer flex items-center gap-1 ${
              tabFilter === 'video'
                ? 'border-blue-600 text-blue-600'
                : 'border-transparent text-[var(--color-ink-muted)] hover:text-[var(--color-ink)]'
            }`}
          >
            <span>📹 {t('shortVideo')}</span>
            <span className="text-[10px] px-1 rounded bg-blue-50 text-blue-600 font-normal">{stats.video_count}</span>
          </button>
          <button
            onClick={() => setTabFilter('note')}
            className={`pb-2 px-2 text-xs font-semibold border-b-2 transition-all cursor-pointer flex items-center gap-1 ${
              tabFilter === 'note'
                ? 'border-purple-600 text-purple-600'
                : 'border-transparent text-[var(--color-ink-muted)] hover:text-[var(--color-ink)]'
            }`}
          >
            <span>🖼️ {t('imageNote')}</span>
            <span className="text-[10px] px-1 rounded bg-purple-50 text-purple-600 font-normal">{stats.note_count}</span>
          </button>
        </div>

        {/* 快捷批量选择条 */}
        <div className="flex items-center justify-between px-6 py-2.5 bg-black/[0.01] border-b border-[var(--color-border)] text-xs">
          <div className="flex items-center gap-2">
            <button
              onClick={allDisplayedChecked ? handleClearSelection : handleSelectAll}
              className="text-xs text-[var(--color-ink-soft)] hover:text-accent flex items-center gap-1 cursor-pointer transition-colors"
            >
              <span className={`text-sm ${allDisplayedChecked ? 'text-accent' : 'text-[var(--color-ink-muted)]'}`}>
                {allDisplayedChecked ? '☑' : '☐'}
              </span>
              {t('selectAllCategory', { count: currentTotal })}
            </button>
            <span className="text-[var(--color-border)]">|</span>
            <button
              onClick={handleSelectVideos}
              className={`px-1.5 py-0.5 rounded text-[11px] transition-colors cursor-pointer ${
                tabFilter === 'video' && selectionMode === 'all'
                  ? 'bg-blue-100 text-blue-700 font-medium'
                  : 'text-blue-600 hover:bg-blue-50'
              }`}
            >
              {t('selectVideosOnly', { count: stats.video_count })}
            </button>
            <button
              onClick={handleSelectNotes}
              className={`px-1.5 py-0.5 rounded text-[11px] transition-colors cursor-pointer ${
                tabFilter === 'note' && selectionMode === 'all'
                  ? 'bg-purple-100 text-purple-700 font-medium'
                  : 'text-purple-600 hover:bg-purple-50'
              }`}
            >
              {t('selectNotesOnly', { count: stats.note_count })}
            </button>
            {hasSelection && (
              <button
                onClick={handleClearSelection}
                className="text-[11px] text-[var(--color-ink-muted)] hover:text-red-500 cursor-pointer ml-1"
              >
                {t('clear')}
              </button>
            )}
          </div>
          <span className="text-accent font-bold text-[11px]">
            {t('selectedCountHint', { count: selectedCount })} {selectionMode === 'all' ? `(${t('rangeModeHint')})` : ''}
          </span>
        </div>

        {/* Content list with Infinite Scroll */}
        <div
          onScroll={handleScroll}
          className="flex-1 overflow-y-auto max-h-[38vh] mx-6 my-2 border border-[var(--color-border)] rounded-xl divide-y divide-[var(--color-border)] subtle-scrollbar"
        >
          {loading ? (
            <div className="flex flex-col items-center justify-center py-12 gap-2 text-xs text-[var(--color-ink-muted)]">
              <span className="animate-spin text-lg">⏳</span> {t('loadingFirstBatch')}
            </div>
          ) : items.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-12 gap-1 text-xs text-[var(--color-ink-muted)]">
              <span>📭</span> {t('noPendingItems', { category: tabFilter === 'video' ? t('shortVideo') : (tabFilter === 'note' ? t('imageNote') : t('categoryContent')) })}
            </div>
          ) : (
            <>
              {items.map(v => {
                const isNote = v.item_type === 'note' || (v.duration ?? 0) === 0;
                const checked = isItemChecked(String(v.id));
                return (
                  <button
                    key={v.platform_item_id}
                    onClick={() => toggleOne(String(v.id))}
                    className="w-full flex items-center gap-2.5 px-3 py-2 text-left hover:bg-black/[0.02] transition-colors cursor-pointer group"
                  >
                    <span className={`text-base flex-shrink-0 ${checked ? 'text-accent' : 'text-[var(--color-ink-muted)]'}`}>
                      {checked ? '☑' : '☐'}
                    </span>
                    <span
                      className={`text-[9px] px-1.5 py-0.2 rounded font-semibold flex-shrink-0 select-none ${
                        v.platform === 'bilibili'
                          ? 'bg-pink-50 text-pink-600 border border-pink-200/60'
                          : 'bg-black/5 text-[var(--color-ink-soft)] border border-black/10'
                      }`}
                    >
                      {v.platform === 'bilibili' ? t('platformBilibili') : t('platformDouyin')}
                    </span>
                    <span
                      className={`text-[9px] px-1.5 py-0.2 rounded font-medium flex-shrink-0 select-none ${
                        isNote
                          ? 'bg-purple-50 text-purple-700 border border-purple-200/60'
                          : 'bg-blue-50 text-blue-700 border border-blue-200/60'
                      }`}
                    >
                      {isNote ? t('tagNote') : t('tagVideo')}
                    </span>
                    <div className="flex-1 min-w-0">
                      <p className="text-xs text-[var(--color-ink)] truncate font-normal group-hover:text-accent transition-colors">
                        {v.title}
                      </p>
                      <p className="text-[10px] text-[var(--color-ink-muted)] mt-0.5">
                        @{v.author}
                        {v.duration ? ` · ${t('duration', { min: Math.floor(v.duration / 60), sec: String(v.duration % 60).padStart(2, '0') })}` : ` · ${t('multiImages')}`}
                      </p>
                    </div>
                  </button>
                );
              })}

              {/* 分批加载状态 / 触底指示器 */}
              {loadingMore && (
                <div className="py-3 text-center text-xs text-accent flex items-center justify-center gap-1.5 bg-accent/5">
                  <span className="animate-spin text-sm">⏳</span> {t('loadingNextBatch', { page })}
                </div>
              )}

              {hasMore && !loadingMore && (
                <button
                  onClick={() => fetchPage(page + 1, true)}
                  className="w-full py-2.5 text-center text-[11px] text-[var(--color-ink-muted)] hover:text-accent hover:bg-black/[0.02] border-t border-[var(--color-border)] cursor-pointer transition-colors"
                >
                  {t('loadMoreHint', { loaded: items.length, total: currentTotal })}
                </button>
              )}

              {!hasMore && items.length > 0 && (
                <div className="py-2 text-center text-[10px] text-[var(--color-ink-muted)] bg-black/[0.01] border-t border-[var(--color-border)] select-none">
                  {t('allLoadedComplete', { count: items.length })}
                </div>
              )}
            </>
          )}
        </div>

        {/* 动态流水线说明 */}
        <div className="mx-6 px-3 py-2 rounded-xl text-xs bg-black/[0.02] border border-[var(--color-border)] flex flex-col gap-1 text-[var(--color-ink-soft)]">
          <div className="flex items-center justify-between font-medium text-[11px]">
            <span className="text-[var(--color-ink)]">{t('pipelineDiffTitle')}</span>
            <span className="text-[10px] text-[var(--color-ink-muted)]">
              {selectionMode === 'all' ? t('rangeModeDetailed') : t('customSelectionMode')}
            </span>
          </div>
          {selectedVideoCount > 0 && (
            <div className="text-[11px] text-blue-700 flex items-center gap-1.5">
              <span className="font-semibold">📹 {t('shortVideo')} ({selectedVideoCount}):</span>
              <span className="opacity-90">{t('pipelineVideoSteps')}</span>
            </div>
          )}
          {selectedNoteCount > 0 && (
            <div className="text-[11px] text-purple-700 flex items-center gap-1.5">
              <span className="font-semibold">🖼️ {t('imageNote')} ({selectedNoteCount}):</span>
              <span className="opacity-90">{t('pipelineNoteSteps')}</span>
            </div>
          )}
          {selectedCount === 0 && (
            <div className="text-[11px] text-[var(--color-ink-muted)]">
              {t('pipelineSelectHint')}
            </div>
          )}
        </div>

        {/* Actions */}
        <div className="flex items-center justify-between px-6 py-3.5 border-t border-[var(--color-border)] mt-2">
          <div className="text-[11px] text-[var(--color-ink-muted)]">
            {t('readyStats', { video: selectedVideoCount, note: selectedNoteCount })}
          </div>
          <div className="flex gap-2">
            <button
              onClick={onClose}
              className="px-4 py-1.5 rounded-full text-xs text-[var(--color-ink-soft)] border border-[var(--color-border)] hover:bg-black/3 transition-colors cursor-pointer"
            >
              {t('cancel')}
            </button>
            <button
              onClick={handleConfirm}
              disabled={building || !hasSelection}
              className="px-5 py-1.5 rounded-full bg-gradient-to-r from-accent to-accent-hover text-white text-xs font-bold shadow-sm hover:shadow transition-all disabled:opacity-40 cursor-pointer flex items-center gap-1"
            >
              {building ? t('submitting') : t('confirmBuildWithCount', { count: selectedCount })}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
