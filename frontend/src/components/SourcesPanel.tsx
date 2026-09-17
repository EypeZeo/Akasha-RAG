import { useState, useEffect, useCallback, useRef } from 'react';
import * as api from '../api';
import BuildConfirmModal from './BuildConfirmModal';
import ExportModal from './ExportModal';
import ExportProgressCard from './ExportProgressCard';
import ApiKeyMissingModal from './ApiKeyMissingModal';
import { useI18n } from '../i18n';
import { VIDEOS_PER_PAGE_OPTIONS } from '../utils/settings';
import { aggregateSyncCounts, getFailedPlatforms } from '../utils/syncSummary';
import { useWorkspaceStore } from '../store/workspace';
import { useExportFlow } from '../hooks/useExportFlow';
import { useBuildFlow } from '../hooks/useBuildFlow';

interface Props {
  onBuildDone: () => void;
  selectedId: string;
  onSelectCollection: (id: string) => void;
  statsRefreshKey: number;
  /** 收藏夹分类每页显示条数；0 表示不分页、全部显示 */
  collectionsPerPage: number;
  /** 收藏夹作品每页显示条数 */
  videosPerPage: number;
  onOpenSettings: () => void;
}

export default function SourcesPanel({
  onBuildDone,
  selectedId,
  onSelectCollection,
  statsRefreshKey,
  collectionsPerPage,
  videosPerPage,
  onOpenSettings,
}: Props) {
  const { t } = useI18n();
  const platformFilter = useWorkspaceStore(s => s.selectedPlatform);
  const setSelectedPlatform = useWorkspaceStore(s => s.setSelectedPlatform);
  const [collections, setCollections] = useState<api.CollectionItem[]>([]);
  const [stats, setStats] = useState<any>(null);
  const [syncing, setSyncing] = useState(false);
  const [showBuildConfirm, setShowBuildConfirm] = useState(false);
  const [buildInitialType, setBuildInitialType] = useState<'all' | 'video' | 'note'>('all');
  const [showApiKeyMissing, setShowApiKeyMissing] = useState(false);
  // 提交中互斥：syncKnowledge() 还没返回时 building 仍是 false，这段窗口
  // 里第二次点击（比如两个失败视频各点一次重试）完全不会被 `if (building...)`
  // 拦住，会各自发起一次 syncKnowledge、各自在后端创建一个任务。isSubmitting
  // 给 UI 用（驱动按钮 disabled/文案）；isSubmittingRef 才是真正挡住重入的
  // 那个——React state 更新不是同步的，同一个事件循环轮次内的第二次点击
  // 靠 state 挡不住，ref 的赋值是立即生效的。
  const [isSubmitting, setIsSubmitting] = useState(false);
  const isSubmittingRef = useRef(false);

  // 展开收藏夹与分页
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [expandedVideos, setExpandedVideos] = useState<api.VideoItem[]>([]);
  const [loadingVideos, setLoadingVideos] = useState(false);
  const [videoPage, setVideoPage] = useState(1);
  const [videoPageSize, setVideoPageSize] = useState(videosPerPage);
  const [videoTotal, setVideoTotal] = useState(0);
  // 整栏（非当前页）视频/图文数量，来自服务端，用于分类计数与"是否隐藏分类筛选行"的判定
  const [expandedVideoCount, setExpandedVideoCount] = useState(0);
  const [expandedNoteCount, setExpandedNoteCount] = useState(0);
  const [videoSearch, setVideoSearch] = useState('');
  const videoCursorsRef = useRef<Map<number, string | undefined>>(new Map([[1, undefined]]));
  const [debouncedSearch, setDebouncedSearch] = useState('');
  const [typeFilter, setTypeFilter] = useState<'all' | 'video' | 'note'>('all');
  const [collectionPage, setCollectionPage] = useState(1);
  const [clearing, setClearing] = useState(false);

  // 批量导出后台任务（进度常驻，弹窗关 / F5 刷新都能恢复）
  const {
    exportTask,
    showExportModal,
    openExportModal,
    closeExportModal,
    startExportPolling,
    dismissExportCard,
    triggerBrowserDownload,
  } = useExportFlow(t);

  // 搜索框 300ms (0.3s) 防抖优化
  useEffect(() => {
    const timer = setTimeout(() => {
      setDebouncedSearch(videoSearch);
    }, 300);
    return () => clearTimeout(timer);
  }, [videoSearch]);

  const fetchCollections = useCallback(async (plat?: string) => {
    try {
      const activePlat = plat !== undefined ? plat : platformFilter;
      const r = await api.listCollections(activePlat);
      if (r.success) setCollections(r.items);
    } catch {}
  }, [platformFilter]);

  const fetchStats = useCallback(async () => {
    try {
      const r = await api.getKnowledgeStats();
      if (r.success) setStats(r);
    } catch {}
  }, []);

  useEffect(() => {
    fetchCollections();
    fetchStats();
  }, [fetchCollections, fetchStats, statsRefreshKey]);

  // 用户在设置里调整「作品每页显示」→ 同步页大小、失效旧游标、必要时按新页大小重取第 1 页
  useEffect(() => {
    setVideoPageSize(videosPerPage);
    setVideoPage(1);
    videoCursorsRef.current = new Map([[1, undefined]]);
    if (expandedId) {
      fetchVideos(expandedId, 1, videosPerPage);
    }
    // fetchVideos / expandedId 故意不入依赖：只在持久化设置变化时触发
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [videosPerPage]);

  // 平台切换或收藏夹数量变化后，把分类分页复位到第 1 页
  useEffect(() => {
    setCollectionPage(1);
  }, [platformFilter, collectionsPerPage, collections.length]);

  // 当收藏夹列表为空时自适应轻量轮询检测（针对初次扫码后后台异步持久化的场景），免去用户手动 F5 刷新
  useEffect(() => {
    if (collections.length > 0) return;
    let attempts = 0;
    let isActive = true;
    const timer = setInterval(async () => {
      if (!isActive) return;
      attempts++;
      if (attempts > 12) {
        clearInterval(timer);
        return;
      }
      try {
        const r = await api.listCollections(platformFilter);
        if (r.success && r.items && r.items.length > 0) {
          if (isActive) {
            setCollections(r.items);
            fetchStats();
          }
          clearInterval(timer);
        }
      } catch {}
    }, 2000);
    return () => {
      isActive = false;
      clearInterval(timer);
    };
  }, [collections.length, fetchStats, platformFilter]);

  const handleSync = async () => {
    setSyncing(true);
    try {
      const r = await api.syncFavorites(platformFilter);
      if (r.success) {
        await fetchCollections();
        await fetchStats();
        if (expandedId) await fetchVideos(expandedId, videoPage, videoPageSize, platformFilter);
        // UI-01: when platform="all", the backend returns BOTH a top-level
        // aggregate (added_videos, ...) AND a `results[]` breakdown for the
        // same numbers — summing both double-counts. aggregateSyncCounts()
        // picks exactly one source depending on the response shape.
        const { addedVideos: addedV, removedVideos: removedV, addedNotes: addedN, removedNotes: removedN, invalidCount } =
          aggregateSyncCounts(r);

        let successMsg: string;
        if (addedV === 0 && removedV === 0 && addedN === 0 && removedN === 0 && invalidCount === 0) {
          successMsg = t('syncUpToDate');
        } else {
          const parts: string[] = [];
          if (addedV > 0 && removedV > 0) {
            parts.push(t('addAndRemoveVideos', { add: addedV, remove: removedV }));
          } else if (addedV > 0) {
            parts.push(t('addVideos', { count: addedV }));
          } else if (removedV > 0) {
            parts.push(t('removeVideos', { count: removedV }));
          }

          if (addedN > 0 && removedN > 0) {
            parts.push(t('addAndRemoveNotes', { add: addedN, remove: removedN }));
          } else if (addedN > 0) {
            parts.push(t('addNotes', { count: addedN }));
          } else if (removedN > 0) {
            parts.push(t('removeNotes', { count: removedN }));
          }

          if (invalidCount > 0) {
            parts.push(t('syncInvalidCount', { count: invalidCount }));
          }

          successMsg = `${t('syncSuccessPrefix')}${parts.join('，')}`;
        }

        // platform="all" 时一个平台失败、另一个成功仍然算 r.success（真实
        // 发生的数据没有理由不刷新），但不能让这句"同步完成"的提示掩盖掉
        // 失败平台——用 platform_results 点名，不猜测、不吞掉。
        const failedPlatforms = getFailedPlatforms(r);
        if (failedPlatforms.length > 0) {
          const platformNames = failedPlatforms
            .map((p) => (p === 'bilibili' ? t('platformBilibili') : t('platformDouyin')))
            .join('、');
          alert(`${successMsg}${t('syncPartialFailureSuffix', { platforms: platformNames })}`);
        } else {
          alert(successMsg);
        }
      } else {
        console.error('Sync failed:', r.message);
        alert(t('syncFailed'));
      }
    } catch (e: any) {
      console.error('Sync failed:', e);
      alert(t('syncFailed'));
    } finally {
      setSyncing(false);
    }
  };

  const openBuildModal = async (type: 'all' | 'video' | 'note' = 'all') => {
    try {
      const status = await api.getSettingsStatus();
      if (!status.ingest_ready) {
        setShowApiKeyMissing(true);
        return;
      }
    } catch {
      // Status check failing (e.g. offline) shouldn't block the user from
      // trying to build — the real ingest call will surface its own error.
    }
    setBuildInitialType(type);
    setShowBuildConfirm(true);
  };

  const fetchVideos = useCallback(async (collectionId: string, page: number, size: number, plat?: string) => {
    setLoadingVideos(true);
    try {
      if (page === 1) videoCursorsRef.current = new Map([[1, undefined]]);
      const activePlat = plat !== undefined ? plat : platformFilter;
      const r = await api.listCollectionVideos(
        collectionId, page, size, activePlat, videoCursorsRef.current.get(page),
      );
      if (r.success) {
        setExpandedVideos(r.items);
        setVideoTotal(r.total);
        setExpandedVideoCount(r.video_count ?? 0);
        setExpandedNoteCount(r.note_count ?? 0);
        setVideoPage(page);
        if (r.next_cursor) videoCursorsRef.current.set(page + 1, r.next_cursor);
      }
    } catch (e) {
      console.error('加载视频列表失败:', e);
    }
    setLoadingVideos(false);
  }, [platformFilter]);

  const onBuildComplete = useCallback(() => {
    onBuildDone();
    fetchStats();
    if (expandedId) fetchVideos(expandedId, videoPage, videoPageSize);
  }, [onBuildDone, fetchStats, fetchVideos, expandedId, videoPage, videoPageSize]);

  const {
    building,
    buildProgress,
    buildTotal,
    buildMessage,
    buildTaskId,
    cancelling,
    startBuildPolling,
    setBuildTotalHint,
    handleCancelBuild,
  } = useBuildFlow(t, onBuildComplete);

  const handleBuild = async (
    selectedIds?: string[],
    contentType?: 'all' | 'video' | 'note',
    scope: 'all' | 'selected' = 'all',
    buildPlat?: string,
  ) => {
    if (building || isSubmittingRef.current || (scope === 'selected' && !selectedIds?.length)) return;
    isSubmittingRef.current = true;
    setIsSubmitting(true);
    const isAll = scope === 'all';
    const initialTotal = isAll ? (stats?.video_cache?.pending ?? 0) : selectedIds!.length;
    setBuildTotalHint(initialTotal);
    const typeLabel = contentType === 'video' ? t('shortVideo') : (contentType === 'note' ? t('imageNote') : t('categoryContent'));
    try {
      const r = await api.syncKnowledge({
        scope: isAll ? 'all' : 'selected',
        collectionId: expandedId || selectedId || 'all',
        contentType: contentType || 'all',
        selectedIds: isAll ? [] : selectedIds,
        platform: buildPlat || platformFilter,
      });
      if (r.success && r.task_id) {
        if (r.pending_count) setBuildTotalHint(r.pending_count);
        startBuildPolling(r.task_id, typeLabel);
      } else if (r.message) {
        console.error('Ingest failed:', r.message);
        alert(t('operationFailed'));
      }
    } catch (e: any) {
      console.error('Ingest failed:', e);
      alert(t('operationFailed'));
    } finally {
      isSubmittingRef.current = false;
      setIsSubmitting(false);
    }
  };

  const handlePlatformChange = (newPlatform: 'all' | 'douyin' | 'bilibili') => {
    setSelectedPlatform(newPlatform);
    fetchCollections(newPlatform);
    if (expandedId) {
      fetchVideos(expandedId, 1, videoPageSize, newPlatform);
    }
  };

  const handleCollectionClick = async (collectionId: string) => {
    onSelectCollection(collectionId);
    if (expandedId === collectionId) {
      setExpandedId(null);
      return;
    }
    setExpandedId(collectionId);
    setVideoSearch('');
    setTypeFilter('all');
    fetchVideos(collectionId, 1, videoPageSize);
  };

  const handlePageChange = (newPage: number) => {
    if (!expandedId) return;
    setVideoSearch(''); // 切换分页时清空搜索，避免混淆
    fetchVideos(expandedId, newPage, videoPageSize);
  };

  const handlePageSizeChange = (newSize: number) => {
    setVideoPageSize(newSize);
    if (!expandedId) return;
    setVideoSearch(''); // 调整每页数量时清空搜索
    fetchVideos(expandedId, 1, newSize);
  };

  const handleExport = (platformItemId: string, platform: string, mode: 'original' | 'ai') => {
    const url = `/api/knowledge/export/${encodeURIComponent(platformItemId)}?mode=${mode}&platform=${encodeURIComponent(platform)}`;
    window.open(url, '_blank');
  };

  const handleDelete = async (platformItemId: string, platform: string) => {
    if (!confirm(t('deleteIngestConfirm'))) return;
    try {
      await api.deleteVideo(platformItemId, platform);
      fetchStats();
      if (expandedId) fetchVideos(expandedId, videoPage, videoPageSize);
    } catch (e: any) {
      console.error('Delete ingested data failed:', e);
      alert(t('operationFailed'));
    }
  };

  const handleClearAll = async () => {
    const scopeMsg = expandedId && expandedId !== 'all' ? t('clearScopeCurrent') : t('clearScopeAll');
    if (!confirm(t('clearKnowledgeConfirm', { scope: scopeMsg }))) return;

    setClearing(true);
    try {
      const r = await api.clearAllKnowledge(expandedId && expandedId !== 'all' ? expandedId : undefined, platformFilter);
      if (r.success) {
        alert(t('clearKnowledgeSuccess', { count: r.reset_count }));
        await fetchStats();
        if (expandedId) fetchVideos(expandedId, 1, videoPageSize);
        onBuildDone();
      } else {
        alert(t('operationFailed'));
      }
    } catch (e: any) {
      console.error('Clear ingested data failed:', e);
      alert(t('operationFailed'));
    } finally {
      setClearing(false);
    }
  };

  const doneCount = stats?.video_cache?.done ?? 0;
  const failedCount = stats?.video_cache?.failed ?? 0;
  const processingCount = (stats?.video_cache?.downloading ?? 0) + (stats?.video_cache?.transcribing ?? 0);
  const pendingCount = stats?.video_cache?.pending ?? 0;
  const isStuck = processingCount > 0 || (failedCount > 0 && pendingCount === 0 && processingCount === 0);

  const totalVideo = stats?.detail?.video?.total ?? stats?.content_types?.total_video ?? stats?.video_cache?.total_video ?? 0;
  const totalNote = stats?.detail?.note?.total ?? stats?.content_types?.total_note ?? stats?.video_cache?.total_note ?? 0;

  const videoDone = stats?.detail?.video?.done ?? 0;
  const videoPending = stats?.detail?.video?.pending ?? 0;

  const noteDone = stats?.detail?.note?.done ?? 0;
  const notePending = stats?.detail?.note?.pending ?? 0;

  // 修正总计统计：避免 Object.values 将 total_video / total_note 重复累加导致翻倍
  const totalCount = (totalVideo + totalNote > 0)
    ? (totalVideo + totalNote)
    : (doneCount + pendingCount + failedCount + processingCount);

  // 分类计数：优先用服务端整栏口径（跨分页稳定）；服务端字段缺失时回退到当前页统计
  const pageVideoCount = expandedVideos.filter(v => (v.item_type === 'video' || (v.duration ?? 0) > 0)).length;
  const pageNoteCount = expandedVideos.filter(v => (v.item_type === 'note' || (v.duration ?? 0) === 0)).length;
  const videoItemsCount = expandedVideoCount || pageVideoCount;
  const noteItemsCount = expandedNoteCount || pageNoteCount;
  // 整栏没有任何图文（B站的常态，也含恰好全是视频的抖音收藏夹）时，隐去分类筛选行
  const hasNotesInCollection = expandedNoteCount > 0 || pageNoteCount > 0;

  // 展开收藏夹内容的本地关键词搜索与分类筛选 (300ms 防抖)
  const filteredVideos = expandedVideos.filter(v => {
    const isNote = v.item_type === 'note' || (v.duration ?? 0) === 0;
    if (typeFilter === 'video' && isNote) return false;
    if (typeFilter === 'note' && !isNote) return false;
    if (!debouncedSearch.trim()) return true;
    const q = debouncedSearch.trim().toLowerCase();
    return v.title.toLowerCase().includes(q) || (v.author && v.author.toLowerCase().includes(q));
  });

  const totalPages = Math.ceil(videoTotal / videoPageSize) || 1;
  const currentCollection = collections.find(c => c.collection_id === (expandedId || selectedId));
  const currentTitle = currentCollection?.title === '全部收藏' ? t('allFavorites') : (currentCollection?.title || t('allFavorites'));

  // 收藏夹分类客户端分页：合成「全部收藏」行恒钉在最前、不参与翻页
  const allRow = collections.filter(c => c.collection_id === 'all');
  const realCollections = collections.filter(c => c.collection_id !== 'all');
  const collectionPageSize = collectionsPerPage > 0 ? collectionsPerPage : realCollections.length || 1;
  const collectionTotalPages = Math.max(1, Math.ceil(realCollections.length / collectionPageSize));
  const safeCollectionPage = Math.min(collectionPage, collectionTotalPages);
  const pagedRealStart = (safeCollectionPage - 1) * collectionPageSize;
  const pagedReal = realCollections.slice(pagedRealStart, pagedRealStart + collectionPageSize);
  // 选中/展开的收藏夹若不在当前页，追加钉住一行，避免"看不见自己选的收藏夹"
  const stickyId = expandedId || selectedId;
  const stickyExtra =
    stickyId && stickyId !== 'all' && !pagedReal.some(c => c.collection_id === stickyId)
      ? realCollections.filter(c => c.collection_id === stickyId)
      : [];
  const visibleCollections = [...allRow, ...stickyExtra, ...pagedReal];
  const showCollectionPager = collectionsPerPage > 0 && collectionTotalPages > 1;

  return (
    <div className="h-full flex flex-col bg-[var(--color-panel)] border-r border-[var(--color-border)]">
      <div className="flex-1 flex flex-col min-h-0 p-4 gap-3 overflow-y-auto">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <svg
              className="w-4 h-4 text-accent shrink-0"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" />
            </svg>
            <h2 className="text-sm font-bold text-[var(--color-ink)]">{t('myFavorites')}</h2>
          </div>
          <div className="flex items-center gap-1.5">
            <button
              onClick={() => openExportModal()}
              disabled={doneCount === 0}
              className="group flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium text-accent bg-accent/10 hover:bg-accent/18 active:scale-95 transition-all duration-200 disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:bg-accent/10 shadow-2xs cursor-pointer"
              title={t('batchExportTooltip')}
            >
              <svg
                className="w-3.5 h-3.5 text-accent shrink-0 transition-transform duration-200 group-hover:-translate-y-0.5"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                <polyline points="17 8 12 3 7 8" />
                <line x1="12" y1="3" x2="12" y2="15" />
              </svg>
              <span>{t('export')}</span>
            </button>
            <button
              onClick={handleSync}
              disabled={syncing}
              className={`group relative overflow-hidden flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium transition-all duration-300 ${
                syncing
                  ? 'bg-accent/12 text-accent border border-accent/40 animate-sync-glow cursor-wait select-none'
                  : 'text-[var(--color-ink-soft)] bg-black/4 hover:bg-black/7 hover:text-[var(--color-ink)] border border-transparent shadow-2xs cursor-pointer active:scale-95'
              }`}
              title={syncing ? t('syncing') : t('sync')}
            >
              {syncing && (
                <span
                  className="absolute inset-0 pointer-events-none bg-gradient-to-r from-transparent via-white/40 to-transparent animate-sync-shimmer"
                  aria-hidden="true"
                />
              )}

              {syncing ? (
                <>
                  <svg
                    className="w-3.5 h-3.5 animate-spin text-accent shrink-0"
                    viewBox="0 0 24 24"
                    fill="none"
                  >
                    <circle
                      className="opacity-25"
                      cx="12"
                      cy="12"
                      r="10"
                      stroke="currentColor"
                      strokeWidth="3"
                    />
                    <path
                      className="opacity-90"
                      fill="currentColor"
                      d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
                    />
                  </svg>
                  <span className="font-semibold tracking-wide">{t('syncing')}</span>
                  <span className="relative flex h-2 w-2 ml-0.5">
                    <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-accent opacity-75" />
                    <span className="relative inline-flex rounded-full h-2 w-2 bg-accent" />
                  </span>
                </>
              ) : (
                <>
                  <svg
                    className="w-3.5 h-3.5 text-[var(--color-ink-muted)] group-hover:text-accent group-hover:rotate-180 transition-all duration-500 shrink-0"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  >
                    <path d="M21 2v6h-6" />
                    <path d="M3 12a9 9 0 0 1 15-6.7L21 8" />
                    <path d="M3 22v-6h6" />
                    <path d="M21 12a9 9 0 0 1-15 6.7L3 16" />
                  </svg>
                  <span>{t('sync')}</span>
                </>
              )}
            </button>
          </div>
        </div>

        <hr className="border-[var(--color-border)]" />

        {/* Platform Filter Tabs */}
        <div className="flex items-center gap-1 p-1 bg-black/[0.03] rounded-xl text-xs">
          <button
            type="button"
            onClick={() => handlePlatformChange('all')}
            className={`flex-1 py-1 px-1.5 rounded-lg font-semibold transition-all cursor-pointer text-center ${
              platformFilter === 'all'
                ? 'bg-white shadow-2xs text-[var(--color-ink)]'
                : 'text-[var(--color-ink-muted)] hover:text-[var(--color-ink)]'
            }`}
          >
            {t('platformAll')}
          </button>
          <button
            type="button"
            onClick={() => handlePlatformChange('douyin')}
            className={`flex-1 py-1 px-1.5 rounded-lg font-semibold transition-all cursor-pointer text-center flex items-center justify-center gap-1.5 ${
              platformFilter === 'douyin'
                ? 'bg-white shadow-2xs text-accent'
                : 'text-[var(--color-ink-muted)] hover:text-[var(--color-ink)]'
            }`}
          >
            <img src="/platform-icons/douyin.svg" alt="" className="w-3.5 h-3.5 object-contain shrink-0" />
            <span>{t('platformDouyin')}</span>
          </button>
          <button
            type="button"
            onClick={() => handlePlatformChange('bilibili')}
            className={`flex-1 py-1 px-1.5 rounded-lg font-semibold transition-all cursor-pointer text-center flex items-center justify-center gap-1.5 ${
              platformFilter === 'bilibili'
                ? 'bg-white shadow-2xs text-pink-600'
                : 'text-[var(--color-ink-muted)] hover:text-[var(--color-ink)]'
            }`}
          >
            <img src="/platform-icons/bilibili.svg" alt="" className="w-3.5 h-3.5 object-contain shrink-0" />
            <span>{t('platformBilibili')}</span>
          </button>
        </div>

        {/* Collections */}
        <div className="flex flex-col gap-2">
          {visibleCollections.map(col => {
            const isSelected = selectedId === col.collection_id;
            const isExpanded = expandedId === col.collection_id;
            const displayTitle = col.title === '全部收藏' ? t('allFavorites') : col.title;
            return (
              <div key={col.collection_id} className="rounded-xl transition-all">
                <button
                  onClick={() => handleCollectionClick(col.collection_id)}
                  className={`w-full text-left p-2.5 rounded-xl transition-all border ${
                    isSelected
                      ? 'border-accent/40 bg-accent-light shadow-sm'
                      : 'border-transparent hover:border-[var(--color-border)] hover:bg-white/60'
                  }`}
                >
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-1.5 truncate flex-1">
                      {col.platform === 'bilibili' && (
                        <span className="text-[9px] px-1.5 py-0.2 rounded font-semibold bg-pink-50 text-pink-600 border border-pink-200/60 flex-shrink-0">
                          {t('platformBilibili')}
                        </span>
                      )}
                      {col.platform === 'douyin' && (
                        <span className="text-[9px] px-1.5 py-0.2 rounded font-semibold bg-black/5 text-[var(--color-ink-soft)] border border-black/10 flex-shrink-0">
                          {t('platformDouyin')}
                        </span>
                      )}
                      <span className="text-sm font-semibold text-[var(--color-ink)] truncate">{displayTitle}</span>
                    </div>
                    <span className="text-[11px] text-[var(--color-ink-muted)] ml-2 flex-shrink-0 flex items-center gap-1">
                      <span>{col.video_count}</span>
                      <span className="text-[9px]">{isExpanded ? '▲' : '▼'}</span>
                    </span>
                  </div>
                </button>

                {/* Expanded video list */}
                {isExpanded && (
                  <div className="ml-2 mt-1 border-l-2 border-accent/30 pl-2.5 py-1 flex flex-col gap-1.5">
                    {/* 分类筛选与搜索 (0.3s 防抖) */}
                    <div className="flex flex-col gap-1.5 mb-1.5">
                      {expandedVideos.length > 0 && hasNotesInCollection && (
                        <div className="flex items-center gap-1 p-0.5 bg-black/[0.03] rounded-lg text-[10px]">
                          <button
                            onClick={() => setTypeFilter('all')}
                            className={`flex-1 py-1 rounded-md font-medium transition-all cursor-pointer ${
                              typeFilter === 'all'
                                ? 'bg-white shadow-2xs text-accent font-bold'
                                : 'text-[var(--color-ink-muted)] hover:text-[var(--color-ink)]'
                            }`}
                            title={t('paginationLoaded', { page: videoPage, count: expandedVideos.length })}
                          >
                            {t('total')} ({expandedVideos.length})
                          </button>
                          <button
                            onClick={() => setTypeFilter('video')}
                            className={`flex-1 py-1 rounded-md font-medium transition-all flex items-center justify-center gap-0.5 cursor-pointer ${
                              typeFilter === 'video'
                                ? 'bg-white shadow-2xs text-blue-600 font-bold'
                                : 'text-[var(--color-ink-muted)] hover:text-[var(--color-ink)]'
                            }`}
                            title={`${t('shortVideo')}: ${videoItemsCount}`}
                          >
                            <span>📹</span>
                            <span>{t('shortVideo')} ({videoItemsCount})</span>
                          </button>
                          <button
                            onClick={() => setTypeFilter('note')}
                            className={`flex-1 py-1 rounded-md font-medium transition-all flex items-center justify-center gap-0.5 cursor-pointer ${
                              typeFilter === 'note'
                                ? 'bg-white shadow-2xs text-purple-600 font-bold'
                                : 'text-[var(--color-ink-muted)] hover:text-[var(--color-ink)]'
                            }`}
                            title={`${t('imageNote')}: ${noteItemsCount}`}
                          >
                            <span>🖼️</span>
                            <span>{t('imageNote')} ({noteItemsCount})</span>
                          </button>
                        </div>
                      )}

                      {/* 搜索框 */}
                      {videoTotal > 3 && (
                        <div className="flex flex-col gap-1">
                          <div className="relative">
                            <input
                              type="text"
                              value={videoSearch}
                              onChange={e => setVideoSearch(e.target.value)}
                              placeholder={t('searchPlaceholder')}
                              className="w-full text-xs px-2.5 py-1 pr-6 rounded-lg bg-white/70 border border-[var(--color-border)] focus:outline-none focus:border-accent text-[var(--color-ink)] placeholder-[var(--color-ink-muted)]"
                            />
                            {videoSearch && (
                              <button onClick={() => setVideoSearch('')} aria-label={t('clear')} className="absolute right-2 top-1 text-[10px] text-gray-400 cursor-pointer">✕</button>
                            )}
                          </div>
                        </div>
                      )}
                    </div>

                    {loadingVideos ? (
                      <div className="flex items-center justify-center py-4 text-xs text-[var(--color-ink-muted)]">
                        <span className="animate-spin mr-1.5">⏳</span> {t('loadingVideos')}
                      </div>
                    ) : filteredVideos.length === 0 ? (
                      <p className="text-xs text-[var(--color-ink-muted)] py-2 text-center">
                        {videoSearch ? t('noMatchedVideos') : t('noVideosPleaseSync')}
                      </p>
                    ) : (
                      <div className="max-h-[360px] overflow-y-auto pr-1 flex flex-col gap-1 subtle-scrollbar">
                        {filteredVideos.map(v => {
                          const isNote = v.item_type === 'note' || (v.duration ?? 0) === 0;
                          return (
                            <div
                              key={v.platform_item_id}
                              className="flex items-center justify-between p-1.5 rounded-lg hover:bg-black/[0.02] gap-1.5 group text-xs border border-transparent hover:border-black/5 transition-all"
                            >
                              {/* 平台微标 */}
                              <span
                                className={`text-[9px] px-1.5 py-0.2 rounded font-semibold flex-shrink-0 select-none ${
                                  v.platform === 'bilibili'
                                    ? 'bg-pink-50 text-pink-600 border border-pink-200/60'
                                    : 'bg-black/5 text-[var(--color-ink-soft)] border border-black/10'
                                }`}
                              >
                                {v.platform === 'bilibili' ? t('platformBilibili') : t('platformDouyin')}
                              </span>

                              {/* 视频/图文微标 */}
                              <span
                                className={`text-[9px] px-1.5 py-0.2 rounded font-medium flex-shrink-0 select-none ${
                                  isNote
                                    ? 'bg-purple-50 text-purple-600 border border-purple-200/60'
                                    : 'bg-blue-50 text-blue-600 border border-blue-200/60'
                                }`}
                                title={isNote ? t('imageNote') : t('shortVideo')}
                              >
                                {isNote ? t('tagNote') : t('tagVideo')}
                              </span>

                              <a
                                href={v.url || (v.platform === 'bilibili' ? `https://www.bilibili.com/video/${v.platform_item_id}` : `https://www.douyin.com/video/${v.platform_item_id}`)}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="text-[var(--color-ink)] hover:text-accent truncate flex-1 font-normal hover:underline transition-colors flex items-center gap-1 group/title"
                                title={`${v.platform === 'bilibili' ? t('openInBilibili') : t('openInDouyin')}: ${v.title}`}
                              >
                                <span className="truncate">{v.title}</span>
                                <span className="opacity-0 group-hover/title:opacity-60 text-[10px] flex-shrink-0 transition-opacity">↗</span>
                              </a>
                            {v.status === 'done' && (
                              <div className="flex gap-1 flex-shrink-0 opacity-80 group-hover:opacity-100">
                                <button
                                  onClick={() => handleExport(v.platform_item_id, v.platform || 'douyin', 'original')}
                                  className="text-[10px] px-1.5 py-0.5 rounded hover:bg-black/5 text-[var(--color-ink-muted)] cursor-pointer"
                                  title={t('exportOriginalTooltip')}
                                >
                                  📄
                                </button>
                                <button
                                  onClick={() => handleExport(v.platform_item_id, v.platform || 'douyin', 'ai')}
                                  className="text-[10px] px-1.5 py-0.5 rounded hover:bg-black/5 text-[var(--color-ink-muted)] cursor-pointer"
                                  title={t('exportAiTooltip')}
                                >
                                  ✨
                                </button>
                                <button
                                  onClick={() => handleDelete(v.platform_item_id, v.platform || 'douyin')}
                                  className="text-[10px] px-1.5 py-0.5 rounded hover:bg-red-50 text-[var(--color-ink-muted)] hover:text-red-500 cursor-pointer"
                                  title={t('deleteIngestedTooltip')}
                                >
                                  🗑️
                                </button>
                              </div>
                            )}

                            {v.status === 'failed' && (
                              <button
                                onClick={() => handleBuild([String(v.id)], 'all', 'selected', v.platform)}
                                disabled={building || isSubmitting}
                                className="text-[10px] px-1.5 py-0.5 rounded bg-red-50 hover:bg-red-100 text-red-600 font-medium flex items-center gap-0.5 cursor-pointer shadow-2xs"
                                title={t('retryIngestTooltip')}
                              >
                                <span>🔄</span>
                                <span>{t('retry')}</span>
                              </button>
                            )}

                            <span
                              className={`text-[10px] ml-1 px-1.5 py-0.5 rounded-full flex-shrink-0 cursor-help ${
                                v.status === 'done' ? 'bg-success-light text-success font-medium' :
                                v.status === 'pending' ? 'bg-amber-light text-amber' :
                                v.status === 'failed' ? 'bg-red-50 text-red-600 border border-red-200 font-semibold' :
                                'bg-black/5 text-[var(--color-ink-muted)]'
                              }`}
                              title={v.status === 'failed' ? t('contentExtractionFailed') : undefined}
                            >
                              {v.status === 'done' ? t('itemIngested') : v.status === 'pending' ? t('itemPending') : v.status === 'failed' ? t('itemFailed') : v.status}
                            </span>
                            </div>
                          );
                        })}
                      </div>
                    )}

                    {/* 分页控制栏 */}
                    {videoTotal > videoPageSize && (
                      <div className="flex flex-col gap-1.5">
                        {videoSearch && (
                          <div className="flex items-center gap-1 p-1.5 bg-amber-50 border border-amber-200 rounded text-[10px] text-amber-700">
                            <span>⚠️</span>
                            <span>{t('searchCurrentPageHint', { count: expandedVideos.length })}</span>
                          </div>
                        )}
                        <div className="flex items-center justify-between pt-2 border-t border-[var(--color-border)] text-[11px] text-[var(--color-ink-soft)]">
                          <div className="flex items-center gap-1">
                            <button
                              onClick={() => handlePageChange(videoPage - 1)}
                              disabled={videoPage <= 1 || loadingVideos}
                              className="px-2 py-0.5 rounded bg-black/3 hover:bg-black/8 disabled:opacity-30 cursor-pointer"
                            >
                              {t('prevPage')}
                            </button>
                            <span>{t('pageInfo', { current: videoPage, total: totalPages, count: videoTotal })}</span>
                            <button
                              onClick={() => handlePageChange(videoPage + 1)}
                              disabled={videoPage >= totalPages || loadingVideos}
                              className="px-2 py-0.5 rounded bg-black/3 hover:bg-black/8 disabled:opacity-30 cursor-pointer"
                            >
                              {t('nextPage')}
                            </button>
                          </div>
                          <div className="flex items-center gap-1">
                            <select
                              value={videoPageSize}
                              onChange={e => handlePageSizeChange(Number(e.target.value))}
                              className="bg-white/60 border border-[var(--color-border)] rounded text-[10px] px-1 py-0.5"
                            >
                              {VIDEOS_PER_PAGE_OPTIONS.map(n => (
                                <option key={n} value={n}>{t('perPage', { count: n })}</option>
                              ))}
                            </select>
                          </div>
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })}

          {showCollectionPager && (
            <div className="flex items-center justify-center gap-2 pt-1 text-[11px] text-[var(--color-ink-soft)]">
              <button
                onClick={() => setCollectionPage(p => Math.max(1, p - 1))}
                disabled={safeCollectionPage <= 1}
                className="px-2 py-0.5 rounded bg-black/3 hover:bg-black/8 disabled:opacity-30 cursor-pointer"
              >
                {t('prevPage')}
              </button>
              <span>{t('collectionPageInfo', { current: safeCollectionPage, total: collectionTotalPages })}</span>
              <button
                onClick={() => setCollectionPage(p => Math.min(collectionTotalPages, p + 1))}
                disabled={safeCollectionPage >= collectionTotalPages}
                className="px-2 py-0.5 rounded bg-black/3 hover:bg-black/8 disabled:opacity-30 cursor-pointer"
              >
                {t('nextPage')}
              </button>
            </div>
          )}
        </div>

        <div className="gradient-divider my-1" />

        {/* 批量导出进度卡（弹窗关闭 / 刷新页面仍可见） */}
        <ExportProgressCard exportTask={exportTask} onDismiss={dismissExportCard} onDownload={triggerBrowserDownload} />

        {/* Build & Clear Section */}
        <div className="flex flex-col gap-2.5">
          <div className="flex items-center justify-between">
            <h3 className="text-xs font-semibold text-[var(--color-ink-soft)]">📥 {building ? t('ingesting') : (isSubmitting ? t('ingestSubmitting') : t('kbStatus'))}</h3>
            {doneCount > 0 && !building && !isSubmitting && (
              <button
                onClick={handleClearAll}
                disabled={clearing}
                className="text-[11px] text-red-500 hover:text-red-700 hover:underline flex items-center gap-1 disabled:opacity-50 cursor-pointer"
                title={t('confirmClearAll')}
              >
                <span>🗑️</span>
                <span>{clearing ? t('cleaning') : t('clearIngested')}</span>
              </button>
            )}
          </div>

          {building || isSubmitting ? (
            building ? (
              <>
                <p className="text-[11px] text-[var(--color-ink)] truncate">{buildMessage}</p>
                <div className="h-2 rounded-full bg-black/5 overflow-hidden">
                  <div
                    className="h-full rounded-full bg-gradient-to-r from-accent to-amber transition-all duration-700 ease-linear"
                    style={{ width: `${buildTotal ? (buildProgress / buildTotal) * 100 : 0}%` }}
                  />
                </div>
                <div className="flex justify-between text-[11px]">
                  <span className="text-[var(--color-ink)] font-semibold">{buildProgress} / {buildTotal}</span>
                  <span className="text-accent font-bold">{buildTotal ? Math.round((buildProgress / buildTotal) * 100) : 0}%</span>
                </div>
                <button
                  onClick={handleCancelBuild}
                  disabled={!buildTaskId || cancelling}
                  className="w-full py-2.5 rounded-xl text-sm font-bold transition-all border border-red-300 text-red-600 bg-red-50 hover:bg-red-100 disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer flex items-center justify-center gap-1"
                >
                  {cancelling ? t('cancelling') : `✕ ${t('cancelIngest')}`}
                </button>
              </>
            ) : (
              // 提交中：还没有 taskId/进度数据可信，只给一句文案，不显示
              // 进度条或取消按钮（没有任务可取消）。
              <p className="text-[11px] text-[var(--color-ink)] truncate">{t('ingestSubmitting')}</p>
            )
          ) : (
            <>
              <div className="flex items-center justify-between text-[11px] text-[var(--color-ink-muted)]">
                <span>
                  ✅ <span className="font-semibold text-[var(--color-ink)]">{doneCount}</span> {t('ingested')} · ⏳ <span className="font-semibold text-[var(--color-ink)]">{pendingCount}</span> {t('pending')}
                </span>
                <span className="font-medium text-[10px] text-[var(--color-ink-muted)]">{t('total')} {totalCount}</span>
              </div>

              {/* 细分展示：短视频与图文笔记独立状态 */}
              {(totalVideo > 0 || totalNote > 0) && (
                <div className="grid grid-cols-2 gap-1.5 p-1.5 rounded-xl bg-black/[0.02] border border-black/5 text-[10px]">
                  {/* 视频卡片 */}
                  <div className="flex flex-col gap-0.5 px-2 py-1 rounded-lg bg-white/70 border border-blue-100 shadow-2xs">
                    <div className="flex items-center justify-between font-semibold text-blue-700">
                      <span className="flex items-center gap-1">{t('tagVideo')}</span>
                      <span className="text-[9px] px-1 rounded bg-blue-50 text-blue-600 font-normal">{totalVideo}</span>
                    </div>
                    <div className="flex justify-between text-[9px] text-[var(--color-ink-muted)] mt-0.5">
                      <span>{t('ingested')} <strong className="text-blue-700">{videoDone}</strong></span>
                      <span>{t('pending')} <strong className={videoPending > 0 ? 'text-amber-600' : 'text-gray-400'}>{videoPending}</strong></span>
                    </div>
                  </div>

                  {/* 图文卡片 */}
                  <div className="flex flex-col gap-0.5 px-2 py-1 rounded-lg bg-white/70 border border-purple-100 shadow-2xs">
                    <div className="flex items-center justify-between font-semibold text-purple-700">
                      <span className="flex items-center gap-1">{t('tagNote')}</span>
                      <span className="text-[9px] px-1 rounded bg-purple-50 text-purple-600 font-normal">{totalNote}</span>
                    </div>
                    <div className="flex justify-between text-[9px] text-[var(--color-ink-muted)] mt-0.5">
                      <span>{t('ingested')} <strong className="text-purple-700">{noteDone}</strong></span>
                      <span>{t('pending')} <strong className={notePending > 0 ? 'text-amber-600' : 'text-gray-400'}>{notePending}</strong></span>
                    </div>
                  </div>
                </div>
              )}

              <div className="h-2 rounded-full bg-black/5 overflow-hidden">
                <div
                  className="h-full rounded-full bg-gradient-to-r from-accent to-amber transition-all duration-500"
                  style={{ width: `${totalCount ? (doneCount / totalCount) * 100 : 0}%` }}
                />
              </div>

              {isStuck && (
                <button
                  onClick={async () => {
                    try {
                      const result = await api.resetFailedVideos();
                      if (!result.success) alert(t('taskStillRunning'));
                      else {
                        fetchStats();
                        if (expandedId) fetchVideos(expandedId, videoPage, videoPageSize);
                      }
                    }
                    catch (e: any) { console.error('Reset failed:', e); alert(t('operationFailed')); }
                  }}
                  className="group w-full py-2 rounded-xl text-xs font-medium text-accent border border-accent/30 hover:bg-accent-light transition-all flex items-center justify-center gap-1.5 cursor-pointer active:scale-98"
                >
                  <svg className="w-3.5 h-3.5 text-accent shrink-0 group-hover:rotate-180 transition-transform duration-500" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M21 2v6h-6" />
                    <path d="M3 12a9 9 0 0 1 15-6.7L21 8" />
                    <path d="M3 22v-6h6" />
                    <path d="M21 12a9 9 0 0 1-15 6.7L3 16" />
                  </svg>
                  <span>{t('resetFailed')}</span>
                </button>
              )}

              <div className="grid grid-cols-2 gap-2 mt-1">
                <button
                  onClick={() => openBuildModal('all')}
                  disabled={pendingCount === 0}
                  className="group py-2.5 rounded-xl bg-gradient-to-r from-accent to-accent-hover text-white text-xs font-bold shadow-sm hover:shadow active:scale-[0.98] transition-all disabled:opacity-40 disabled:cursor-not-allowed flex items-center justify-center gap-1.5 cursor-pointer"
                >
                  <svg className="w-3.5 h-3.5 shrink-0 group-hover:scale-110 transition-transform duration-200" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M4.5 16.5c-1.5 1.26-2 5-2 5s3.74-.5 5-2c.71-.84.7-2.13-.09-2.91a2.18 2.18 0 0 0-2.91-.09z"/>
                    <path d="m12 15-3-3a22 22 0 0 1 2-3.95A12.88 12.88 0 0 1 22 2c0 2.72-.78 7.5-6 11a22.35 22.35 0 0 1-4 2z"/>
                  </svg>
                  <span>{t('oneClickIngest')} ({pendingCount})</span>
                </button>
                <button
                  onClick={() => openExportModal()}
                  disabled={doneCount === 0}
                  className="group py-2.5 rounded-xl border border-accent/35 bg-accent-light hover:bg-accent/20 text-accent text-xs font-bold transition-all duration-200 active:scale-[0.98] disabled:opacity-40 disabled:cursor-not-allowed flex items-center justify-center gap-1.5 shadow-2xs cursor-pointer"
                >
                  <svg className="w-3.5 h-3.5 shrink-0 group-hover:-translate-y-0.5 transition-transform duration-200" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                    <polyline points="17 8 12 3 7 8" />
                    <line x1="12" y1="3" x2="12" y2="15" />
                  </svg>
                  <span>{t('batchExport')} ({doneCount})</span>
                </button>
              </div>

              {/* 仅入库视频 / 仅入库图文 快捷分流入口 */}
              {videoPending > 0 && notePending > 0 && (
                <div className="flex gap-1.5">
                  <button
                    onClick={() => openBuildModal('video')}
                    className="flex-1 py-1.5 px-2 rounded-lg text-[10px] bg-blue-50/80 hover:bg-blue-100 text-blue-700 border border-blue-200/60 font-medium transition-all active:scale-[0.98] cursor-pointer flex items-center justify-center gap-1"
                    title={`${t('onlyIngestVideo')} (${videoPending})`}
                  >
                    <svg className="w-3 h-3 text-blue-600 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <path d="m22 8-6 4 6 4V8Z" />
                      <rect width="14" height="12" x="2" y="6" rx="2" />
                    </svg>
                    <span>{t('onlyIngestVideo')} ({videoPending})</span>
                  </button>
                  <button
                    onClick={() => openBuildModal('note')}
                    className="flex-1 py-1.5 px-2 rounded-lg text-[10px] bg-purple-50/80 hover:bg-purple-100 text-purple-700 border border-purple-200/60 font-medium transition-all active:scale-[0.98] cursor-pointer flex items-center justify-center gap-1"
                    title={`${t('onlyIngestNote')} (${notePending})`}
                  >
                    <svg className="w-3 h-3 text-purple-600 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <rect width="18" height="18" x="3" y="3" rx="2" ry="2" />
                      <circle cx="9" cy="9" r="2" />
                      <path d="m21 15-3.086-3.086a2 2 0 0 0-2.828 0L6 21" />
                    </svg>
                    <span>{t('onlyIngestNote')} ({notePending})</span>
                  </button>
                </div>
              )}
            </>
          )}
        </div>

      </div>

      {showBuildConfirm && (
        <BuildConfirmModal
          pendingCount={pendingCount}
          initialType={buildInitialType}
          collectionId={expandedId || selectedId || 'all'}
          collectionTitle={currentTitle}
          platform={platformFilter}
          onClose={() => setShowBuildConfirm(false)}
          onConfirm={(selectedIds, contentType, scope, buildPlat) => {
            setShowBuildConfirm(false);
            handleBuild(selectedIds, contentType, scope, buildPlat);
          }}
        />
      )}

      {showApiKeyMissing && (
        <ApiKeyMissingModal
          kind="ingest"
          onClose={() => setShowApiKeyMissing(false)}
          onOpenSettings={onOpenSettings}
        />
      )}

      {showExportModal && (
        <ExportModal
          collectionId={expandedId || selectedId}
          collectionTitle={currentTitle}
          doneCount={doneCount}
          onClose={closeExportModal}
          onExportStarted={(taskId, mode) => startExportPolling(taskId, mode)}
        />
      )}
    </div>
  );
}
