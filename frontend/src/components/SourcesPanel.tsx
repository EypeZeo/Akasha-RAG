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
  /** 选中的收藏夹当初是在哪个平台下选的。远端收藏夹 ID 只在平台内唯一，单靠 selectedId 说不清选的是哪一个。 */
  selectedOwner?: string;
  onSelectCollection: (id: string, owner?: string) => void;
  statsRefreshKey: number;
  /** 收藏夹分类每页显示条数；0 表示不分页、全部显示 */
  collectionsPerPage: number;
  /** 收藏夹作品每页显示条数 */
  videosPerPage: number;
  onOpenSettings: () => void;
}

/** 一次收藏夹列表请求最多等多久；超时按失败处理并释放"在途"占位，否则一次挂死的请求会让轮询永远发不出下一次（Issue #27）。 */
const COLLECTIONS_REQUEST_DEADLINE_MS = 7000;
const COLLECTIONS_POLL_INTERVAL_MS = 2000;
const COLLECTIONS_POLL_MAX_ATTEMPTS = 12;

/** 平台的权威当前值：异步续接执行时渲染闭包里的 platformFilter 早已过期，而平台还能在面板之外（ChatPanel）被改掉。 */
const currentPlatform = () => useWorkspaceStore.getState().selectedPlatform;

interface CollectionsRequest {
  platform: string;
  controller: AbortController;
}

interface CollectionsData {
  platform: string;
  items: api.CollectionItem[];
  /** 这个平台一次数据都没拿到（首次加载失败）。 */
  failed: boolean;
  /** 已有这个平台的旧数据，但最近一次刷新失败了：列表可操作，只是可能过期。 */
  stale: boolean;
}

/** 一次视频请求（也是一份已加载视频）的完整作用域。 */
interface VideoScope {
  collectionId: string;
  platform: string;
  page: number;
  pageSize: number;
}

interface VideoData extends VideoScope {
  items: api.VideoItem[];
  total: number;
  /** 整栏（非当前页）视频/图文数量，来自服务端 */
  videoCount: number;
  noteCount: number;
}

/** 收藏夹的身份是（所属平台, 远端 ID），不是裸 ID：两个平台可以有同一个远端 ID。合成的"全部收藏"行没有所属平台。 */
interface CollectionIdentity { id: string; owner: string; title: string }
interface ActionScope { id: string; platform: string; title: string }
const rowIdentity = (row: api.CollectionItem): CollectionIdentity => ({
  id: row.collection_id, owner: row.collection_id === 'all' ? '' : row.platform || '', title: row.title,
});
/** 某行在当前平台视图下是否还有意义：无所属平台的行、"全部"视图、或视图恰好就是它的平台。 */
const compatible = (row: CollectionIdentity | null, view: string) =>
  row !== null && (!row.owner || view === 'all' || row.owner === view);
const rowKey = (row: CollectionIdentity) => JSON.stringify([row.owner, row.id]);
/** 对这一行发起请求时该带的平台：行自己的平台优先，合成行才用当前视图。 */
const requestPlatform = (row: CollectionIdentity, view: string) => row.owner || view;

export default function SourcesPanel({
  onBuildDone,
  selectedId,
  selectedOwner,
  onSelectCollection,
  statsRefreshKey,
  collectionsPerPage,
  videosPerPage,
  onOpenSettings,
}: Props) {
  const { t } = useI18n();
  const platformFilter = useWorkspaceStore(s => s.selectedPlatform);
  const setSelectedPlatform = useWorkspaceStore(s => s.setSelectedPlatform);
  // 列表连同它所属的平台一起存：读取时只认与当前平台一致的那份，平台刚切换、新列表还没到的那一帧，
  // 旧平台的行根本不会被画出来，也点不到（不靠 effect 事后清理）。
  const [collectionsData, setCollectionsData] = useState<CollectionsData | null>(null);
  const collectionsReady = collectionsData?.platform === platformFilter;
  const collections = collectionsReady ? collectionsData.items : [];
  const [stats, setStats] = useState<any>(null);
  const [syncing, setSyncing] = useState(false);
  const [showBuildConfirm, setShowBuildConfirm] = useState(false);
  // 打开确认弹窗那一刻的作用域快照：弹窗开着期间选中项/平台再变，也不影响这次入库/导出的目标。
  const [buildScope, setBuildScope] = useState<ActionScope | null>(null);
  const [exportScope, setExportScope] = useState<ActionScope | null>(null);
  const mountedRef = useRef(true);
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

  // 展开收藏夹与分页。展开的是一个（所属平台, 远端 ID）身份，不是裸 ID；在与它不兼容的平台视图下它算"没展开"。
  const [expandedRow, setExpandedRow] = useState<CollectionIdentity | null>(null);
  const expanded = compatible(expandedRow, platformFilter) ? expandedRow : null;
  const expandedId = expanded?.id ?? null;
  const [loadingVideos, setLoadingVideos] = useState(false);
  const [videoPageSize, setVideoPageSize] = useState(videosPerPage);
  // 已加载的视频连同它所属的完整作用域（平台 + 收藏夹 + 页码 + 每页数量）一起存，渲染时只展示与
  // 「当前平台 + 当前展开的收藏夹」一致的那份：平台在面板之外被切换的那一次渲染里，旧内容在同一帧就不可见。
  const [videoData, setVideoData] = useState<VideoData | null>(null);
  const [videoSearch, setVideoSearch] = useState('');
  const videoCursorsRef = useRef<Map<number, string | undefined>>(new Map([[1, undefined]]));
  // 「此刻」的权威作用域。异步续接（同步/删除/清空/构建完成……）执行时渲染闭包早已过期，一律读这些 ref
  // （handler 里改状态时同步写 ref，再发请求），不读闭包。
  const expandedIdRef = useRef<string | null>(null);
  const expandedRowRef = useRef<CollectionIdentity | null>(null);
  const videoPageSizeRef = useRef(videosPerPage);
  const videoScopeRef = useRef<VideoScope | null>(null);
  const changePageSize = (size: number) => {
    videoPageSizeRef.current = size;
    setVideoPageSize(size);
  };
  const [debouncedSearch, setDebouncedSearch] = useState('');
  const [typeFilter, setTypeFilter] = useState<'all' | 'video' | 'note'>('all');
  // 收藏夹列表页码属于一个"作用域"（平台 + 每页数量）：作用域一变，就在这次渲染里回到第 1 页——渲染期调整
  // state（React 会丢掉这次渲染、立刻带着新 state 重来，用户看不到中间帧），而不是用 effect 去复位。
  // effect 在提交之后才异步执行：用户恰好在"列表已渲染、effect 还没跑"的窗口里点了"下一页"，点击的更新先入队、
  // effect 的复位后入队，结果 1→2→1，点击被吞（Issue #27）。列表长度变化不复位——safeCollectionPage 的钳制已经
  // 覆盖"列表变短"。
  const [collectionPage, setCollectionPage] = useState(1);
  const [pageScope, setPageScope] = useState({ platform: platformFilter, perPage: collectionsPerPage });
  if (pageScope.platform !== platformFilter || pageScope.perPage !== collectionsPerPage) {
    setPageScope({ platform: platformFilter, perPage: collectionsPerPage });
    setCollectionPage(1);
  }
  const [clearing, setClearing] = useState(false);

  // 请求令牌：每次发起 +1，只有仍是"在途那一次"的响应才允许提交状态。收起 / 平台切换 / 卸载也会使在途请求整体失效。
  const activeCollectionsRef = useRef<CollectionsRequest | null>(null);
  const videosReqRef = useRef(0);
  // 卸载：只改 ref、不调用任何 setter，并中止在途的列表请求。
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      activeCollectionsRef.current?.controller.abort();
      activeCollectionsRef.current = null;
      videosReqRef.current += 1;
      videoScopeRef.current = null;
    };
  }, []);

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

  /**
   * 拉取收藏夹列表。不接受平台参数：发起时读 store 的当前平台。任何新请求都会中止在途的那一个
   * （挂载、手动刷新、平台切换一律"以最新意图为准"）；被取代 / 卸载 / 平台已变的响应静默丢弃。
   * 每个请求有 deadline：超时按失败处理并释放在途占位，否则一次挂死的请求会让轮询永远发不出下一次。
   * 返回实际提交的列表；被取代、平台已变、卸载或失败时返回 null。
   */
  const requestCollections = useCallback(async (source: 'mount' | 'poll' | 'manual'): Promise<api.CollectionItem[] | null> => {
    if (!mountedRef.current) return null;
    const platform = currentPlatform();
    activeCollectionsRef.current?.controller.abort();
    const request: CollectionsRequest = { platform, controller: new AbortController() };
    activeCollectionsRef.current = request;
    const isCurrent = () =>
      mountedRef.current && activeCollectionsRef.current === request && currentPlatform() === platform;
    let deadline: ReturnType<typeof setTimeout> | undefined;
    try {
      const timedOut = new Promise<never>((_, reject) => {
        deadline = setTimeout(() => {
          request.controller.abort();
          reject(new Error('Collection request timed out'));
        }, COLLECTIONS_REQUEST_DEADLINE_MS);
      });
      const r = await Promise.race([api.listCollections(platform, request.controller.signal), timedOut]);
      if (!isCurrent()) return null;
      if (!r.success) throw new Error('Collection request failed');
      setCollectionsData({ platform, items: r.items, failed: false, stale: false });
      return r.items;
    } catch {
      if (isCurrent()) {
        setCollectionsData(prev => {
          // 这个平台还没有任何数据：给出"失败"结论，界面不再一直转圈
          if (prev?.platform !== platform) return { platform, items: [], failed: true, stale: false };
          // 轮询是后台自愈，它自己失败不该打扰用户；用户主动触发的刷新失败，才把旧列表标成"可能过期"
          return source === 'poll' ? prev : { ...prev, stale: true };
        });
      }
      return null;
    } finally {
      clearTimeout(deadline);
      if (activeCollectionsRef.current === request) activeCollectionsRef.current = null;
    }
  }, []);

  const fetchStats = useCallback(async () => {
    try {
      const r = await api.getKnowledgeStats();
      if (r.success) setStats(r);
    } catch {}
  }, []);

  /**
   * 拉取展开收藏夹的某一页视频。不接受平台/每页数量参数：一律读此刻的权威值（ref + store）。
   * 发起时若该收藏夹已不是展开的那个，直接不发。提交前确认：仍是最新一次、平台仍适用——
   * 任何一项不成立，视频、计数、spinner、游标一概不写。
   */
  const fetchVideos = useCallback(async (collectionId: string, page: number) => {
    const row = expandedRowRef.current;
    if (!mountedRef.current || !row || collectionId !== row.id || !compatible(row, currentPlatform())) return;
    const scope: VideoScope = {
      collectionId,
      platform: requestPlatform(row, currentPlatform()),
      page,
      pageSize: videoPageSizeRef.current,
    };
    const token = ++videosReqRef.current;
    videoScopeRef.current = scope;
    // 第 1 页换一张新的游标表；其余页沿用同一张
    if (page === 1) videoCursorsRef.current = new Map([[1, undefined]]);
    const cursors = videoCursorsRef.current;
    const isCurrent = () =>
      mountedRef.current && token === videosReqRef.current &&
      compatible(row, currentPlatform()) && scope.platform === requestPlatform(row, currentPlatform());
    // 失败也要给这个作用域一个结论，否则界面会一直转圈：同作用域的旧数据保留，否则提交空列表
    const settleWithoutData = () => setVideoData(prev =>
      prev && prev.collectionId === scope.collectionId && prev.platform === scope.platform
        ? prev
        : { ...scope, items: [], total: 0, videoCount: 0, noteCount: 0 });
    setLoadingVideos(true);
    try {
      const r = await api.listCollectionVideos(collectionId, page, scope.pageSize, scope.platform, cursors.get(page));
      if (!isCurrent()) return;
      if (r.success) {
        setVideoData({
          ...scope,
          items: r.items,
          total: r.total,
          videoCount: r.video_count ?? 0,
          noteCount: r.note_count ?? 0,
        });
        if (r.next_cursor) cursors.set(page + 1, r.next_cursor);
      } else {
        settleWithoutData();
      }
    } catch (e) {
      console.error('加载视频列表失败:', e);
      if (isCurrent()) settleWithoutData();
    } finally {
      if (isCurrent()) setLoadingVideos(false);
    }
  }, []);

  /**
   * 所有「操作完成后刷新展开视频」的续接统一走这里：读此刻展开的收藏夹与所在页，不用发起时的闭包。
   * scopeId / platform：这次操作只针对某个收藏夹/平台（如清空该收藏夹）时传入；用户已经换了目标就不用刷新了。
   */
  const refreshExpandedVideos = useCallback(
    (opts: { scopeId?: string; platform?: string; page?: number } = {}): Promise<void> => {
      const id = expandedIdRef.current;
      if (!id) return Promise.resolve();
      if (opts.scopeId !== undefined && opts.scopeId !== id) return Promise.resolve();
      const row = expandedRowRef.current;
      if (opts.platform && row && opts.platform !== requestPlatform(row, currentPlatform())) return Promise.resolve();
      const scope = videoScopeRef.current;
      return fetchVideos(id, opts.page ?? (scope && scope.collectionId === id ? scope.page : 1));
    },
    [fetchVideos],
  );

  // 收藏夹与统计：挂载、父级刷新键变化、平台变化（含 ChatPanel 等面板之外的切换）时重拉。
  // requestCollections 发起时读 store，所以平台要显式列为依赖。
  useEffect(() => {
    requestCollections('mount');
    fetchStats();
  }, [requestCollections, fetchStats, statsRefreshKey, platformFilter]);

  const changeExpanded = useCallback((row: CollectionIdentity | null) => {
    expandedRowRef.current = row;
    expandedIdRef.current = row?.id ?? null;
    setExpandedRow(row);
  }, []);

  // 平台变化：不兼容的展开项收起（它属于另一个平台，换回来也不该"复活"）；仍适用于新视图的真实行保持展开、
  // 不重载；合成的"全部收藏"行按新平台重拉第 1 页。在途的视频请求随之失效。
  useEffect(() => {
    const row = expandedRowRef.current;
    if (row?.owner && compatible(row, platformFilter)) return;
    videosReqRef.current += 1;
    videoScopeRef.current = null;
    if (row && !compatible(row, platformFilter)) {
      changeExpanded(null);
      setLoadingVideos(false);
    } else {
      refreshExpandedVideos({ page: 1 });
    }
  }, [platformFilter, refreshExpandedVideos, changeExpanded]);

  // 用户在设置里调整「作品每页显示」→ 同步页大小、失效旧游标、必要时按新页大小重取第 1 页
  useEffect(() => {
    videoPageSizeRef.current = videosPerPage;
    setVideoPageSize(videosPerPage);
    videoCursorsRef.current = new Map([[1, undefined]]);
    refreshExpandedVideos({ page: 1 });
  }, [videosPerPage, refreshExpandedVideos]);

  // 当收藏夹列表为空时自适应轻量轮询检测（针对初次扫码后后台异步持久化的场景），免去用户手动 F5 刷新
  useEffect(() => {
    if (collections.length > 0) return;
    let attempts = 0;
    let isActive = true;
    const timer = setInterval(async () => {
      if (!isActive) return;
      // 同一平台已有请求在途（挂载请求、上一次轮询或手动刷新）：这一拍不重复发起，也不消耗次数
      if (activeCollectionsRef.current?.platform === platformFilter) return;
      if (attempts >= COLLECTIONS_POLL_MAX_ATTEMPTS) {
        clearInterval(timer);
        return;
      }
      attempts++;
      const items = await requestCollections('poll');
      if (isActive && items && items.length > 0) {
        fetchStats();
        clearInterval(timer);
      }
    }, COLLECTIONS_POLL_INTERVAL_MS);
    return () => {
      isActive = false;
      clearInterval(timer);
    };
    // platformFilter 只用来重新起算：切到另一个平台后，它的空列表也要有完整的 12 次自愈机会
  }, [collections.length, requestCollections, fetchStats, platformFilter]);

  const handleSync = async () => {
    setSyncing(true);
    try {
      const r = await api.syncFavorites(platformFilter);
      if (r.success) {
        // 续接：刷新「此刻」展示的内容，而不是发起同步时的平台/收藏夹/页码
        await requestCollections('manual');
        await fetchStats();
        await refreshExpandedVideos();
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
    if (!actionScope) return;
    // 点击那一刻的作用域快照：readiness 检查期间选中项/平台再变，这次入库的目标仍是用户点的那个。
    const target = { ...actionScope };
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
    setBuildScope(target);
    setBuildInitialType(type);
    setShowBuildConfirm(true);
  };

  const onBuildComplete = useCallback(() => {
    onBuildDone();
    fetchStats();
    refreshExpandedVideos();
  }, [onBuildDone, fetchStats, refreshExpandedVideos]);

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
    target?: ActionScope,
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
        collectionId: target?.id ?? actionScope?.id ?? 'all',
        contentType: contentType || 'all',
        selectedIds: isAll ? [] : selectedIds,
        platform: target?.platform ?? buildPlat ?? actionScope?.platform ?? platformFilter,
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
    if (newPlatform === currentPlatform()) {
      // 点当前平台 = 显式刷新（AC2 的第三个复位来源）：store 值没变、平台 effect 不会触发，所以这里自己刷，
      // 并回到第 1 页。
      setCollectionPage(1);
      requestCollections('manual');
      refreshExpandedVideos({ page: 1 });
      return;
    }
    // 真正的切换只改 store：收藏夹/统计/展开视频的重拉统一由 [platformFilter] 的 effect 负责，
    // 这样面板之外（ChatPanel）触发的平台切换走的是同一条路径。
    setSelectedPlatform(newPlatform);
  };

  const handleCollectionClick = async (col: api.CollectionItem) => {
    const row = rowIdentity(col);
    onSelectCollection(row.id, row.owner || undefined);
    if (expandedRowRef.current && rowKey(expandedRowRef.current) === rowKey(row)) {
      changeExpanded(null);
      // 收起：让在途的视频请求整体失效
      videosReqRef.current += 1;
      videoScopeRef.current = null;
      setLoadingVideos(false);
      return;
    }
    changeExpanded(row);
    setVideoSearch('');
    setTypeFilter('all');
    fetchVideos(row.id, 1);
  };

  const handlePageChange = (newPage: number) => {
    const id = expandedIdRef.current;
    if (!id) return;
    setVideoSearch(''); // 切换分页时清空搜索，避免混淆
    fetchVideos(id, newPage);
  };

  const handlePageSizeChange = (newSize: number) => {
    changePageSize(newSize);
    const id = expandedIdRef.current;
    if (!id) return;
    setVideoSearch(''); // 调整每页数量时清空搜索
    fetchVideos(id, 1);
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
      refreshExpandedVideos();
    } catch (e: any) {
      console.error('Delete ingested data failed:', e);
      alert(t('operationFailed'));
    }
  };

  const handleClearAll = async () => {
    if (!actionScope) return;
    // 与入库/导出共用同一份已解析的作用域；收起收藏夹不该把"清空这个收藏夹"悄悄放大成"清空全库"。
    const target = { ...actionScope };
    const clearScopeId = target.id !== 'all' ? target.id : undefined;
    const scopeMsg = clearScopeId ? t('clearScopeCurrent') : t('clearScopeAll');
    if (!confirm(t('clearKnowledgeConfirm', { scope: scopeMsg }))) return;

    setClearing(true);
    try {
      const r = await api.clearAllKnowledge(clearScopeId, target.platform);
      if (r.success) {
        alert(t('clearKnowledgeSuccess', { count: r.reset_count }));
        await fetchStats();
        // 只针对某个收藏夹的清空：用户已经切到别的收藏夹就不刷新旧的那个
        refreshExpandedVideos({ scopeId: clearScopeId, platform: target.platform, page: 1 });
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

  // 只展示与「当前平台 + 当前展开的收藏夹」一致的那份已加载视频。作用域对不上的那一帧（例如平台刚在面板之外
  // 被切换、新数据还没到）旧内容根本不会被画出来，视图显示 loading。
  const shownVideos =
    videoData && expanded && videoData.platform === requestPlatform(expanded, platformFilter) && videoData.collectionId === expandedId
      ? videoData
      : null;
  const expandedVideos = shownVideos?.items ?? [];
  const videoTotal = shownVideos?.total ?? 0;
  const videoPage = shownVideos?.page ?? 1;
  // 整栏（非当前页）视频/图文数量，来自服务端，用于分类计数与"是否隐藏分类筛选行"的判定
  const expandedVideoCount = shownVideos?.videoCount ?? 0;
  const expandedNoteCount = shownVideos?.noteCount ?? 0;
  const videosLoading = loadingVideos || (expandedId !== null && shownVideos === null);

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

  // 选中项必须唯一对应一行：没记录所属平台、同一个远端 ID 又出现在多个平台时是"歧义"，不猜第一个。
  const selectedMatches = collections.filter(c =>
    c.collection_id === selectedId && (!selectedOwner || (c.platform || '') === selectedOwner));
  const selectedRow = selectedMatches.length === 1 ? rowIdentity(selectedMatches[0]) : null;
  // 入库 / 导出 / 清空共用的目标：展开的那行优先，其次是选中行。列表还没到 / 加载失败 / 选中歧义 / 与当前平台
  // 不兼容时没有目标（null），所有依赖它的入口都不可点，也不会悄悄退回到"全库"。
  const targetRow: CollectionIdentity | null =
    expanded ?? (selectedId === 'all' ? { id: 'all', owner: '', title: '全部收藏' } : selectedRow);
  const actionScope: ActionScope | null =
    collectionsReady && !collectionsData.failed && targetRow && compatible(targetRow, platformFilter)
      ? {
        id: targetRow.id,
        platform: requestPlatform(targetRow, platformFilter),
        title: targetRow.title === '全部收藏' ? t('allFavorites') : targetRow.title,
      }
      : null;
  const openScopedExport = () => {
    if (!actionScope) return;
    setExportScope({ ...actionScope });
    openExportModal();
  };

  // 收藏夹分类客户端分页：合成「全部收藏」行恒钉在最前、不参与翻页
  const allRow = collections.filter(c => c.collection_id === 'all');
  const realCollections = collections.filter(c => c.collection_id !== 'all');
  const collectionPageSize = collectionsPerPage > 0 ? collectionsPerPage : realCollections.length || 1;
  const collectionTotalPages = Math.max(1, Math.ceil(realCollections.length / collectionPageSize));
  const safeCollectionPage = Math.min(collectionPage, collectionTotalPages);
  // 翻页基于"当前显示的页"（已钳制），而不是 state 里可能已经越界的页码。
  const goToCollectionPage = (delta: number) =>
    setCollectionPage(p => Math.max(1, Math.min(collectionTotalPages, Math.min(p, collectionTotalPages) + delta)));
  const pagedRealStart = (safeCollectionPage - 1) * collectionPageSize;
  const pagedReal = realCollections.slice(pagedRealStart, pagedRealStart + collectionPageSize);
  // 选中/展开的收藏夹若不在当前页，追加钉住一行，避免"看不见自己选的收藏夹"
  const stickyRow = expanded ?? selectedRow;
  const stickyExtra =
    stickyRow && stickyRow.id !== 'all' && !pagedReal.some(c => rowKey(rowIdentity(c)) === rowKey(stickyRow))
      ? realCollections.filter(c => rowKey(rowIdentity(c)) === rowKey(stickyRow))
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
              onClick={openScopedExport}
              disabled={doneCount === 0 || !actionScope}
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
          {!collectionsReady && (
            <div aria-busy="true" aria-label={t('loadingVideos')}>
              {[0, 1, 2].map(i => <div key={i} aria-hidden="true" className="h-10 my-2 rounded-xl bg-black/5 animate-pulse" />)}
            </div>
          )}
          {collectionsReady && collectionsData.failed && (
            <p role="alert" className="text-xs text-[var(--color-ink-muted)]">{t('operationFailed')}</p>
          )}
          {collectionsReady && collectionsData.stale && !collectionsData.failed && (
            <p role="alert" className="text-xs text-amber">{t('collectionsMayBeStale')}</p>
          )}
          {visibleCollections.map(col => {
            const identity = rowIdentity(col);
            const isSelected = selectedRow !== null && rowKey(selectedRow) === rowKey(identity);
            const isExpanded = expanded !== null && rowKey(expanded) === rowKey(identity);
            const displayTitle = col.title === '全部收藏' ? t('allFavorites') : col.title;
            return (
              <div key={rowKey(identity)} className="rounded-xl transition-all">
                <button
                  onClick={() => handleCollectionClick(col)}
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

                    {videosLoading ? (
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
                              disabled={videoPage <= 1 || videosLoading}
                              className="px-2 py-0.5 rounded bg-black/3 hover:bg-black/8 disabled:opacity-30 cursor-pointer"
                            >
                              {t('prevPage')}
                            </button>
                            <span>{t('pageInfo', { current: videoPage, total: totalPages, count: videoTotal })}</span>
                            <button
                              onClick={() => handlePageChange(videoPage + 1)}
                              disabled={videoPage >= totalPages || videosLoading}
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
                onClick={() => goToCollectionPage(-1)}
                disabled={safeCollectionPage <= 1}
                className="px-2 py-0.5 rounded bg-black/3 hover:bg-black/8 disabled:opacity-30 cursor-pointer"
              >
                {t('prevPage')}
              </button>
              <span>{t('collectionPageInfo', { current: safeCollectionPage, total: collectionTotalPages })}</span>
              <button
                onClick={() => goToCollectionPage(1)}
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
                disabled={clearing || !actionScope}
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
                        refreshExpandedVideos();
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
                  disabled={pendingCount === 0 || !actionScope}
                  className="group py-2.5 rounded-xl bg-gradient-to-r from-accent to-accent-hover text-white text-xs font-bold shadow-sm hover:shadow active:scale-[0.98] transition-all disabled:opacity-40 disabled:cursor-not-allowed flex items-center justify-center gap-1.5 cursor-pointer"
                >
                  <svg className="w-3.5 h-3.5 shrink-0 group-hover:scale-110 transition-transform duration-200" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M4.5 16.5c-1.5 1.26-2 5-2 5s3.74-.5 5-2c.71-.84.7-2.13-.09-2.91a2.18 2.18 0 0 0-2.91-.09z"/>
                    <path d="m12 15-3-3a22 22 0 0 1 2-3.95A12.88 12.88 0 0 1 22 2c0 2.72-.78 7.5-6 11a22.35 22.35 0 0 1-4 2z"/>
                  </svg>
                  <span>{t('oneClickIngest')} ({pendingCount})</span>
                </button>
                <button
                  onClick={openScopedExport}
                  disabled={doneCount === 0 || !actionScope}
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
                    disabled={!actionScope}
                    className="flex-1 py-1.5 px-2 rounded-lg text-[10px] bg-blue-50/80 hover:bg-blue-100 text-blue-700 border border-blue-200/60 font-medium transition-all active:scale-[0.98] disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer flex items-center justify-center gap-1"
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
                    disabled={!actionScope}
                    className="flex-1 py-1.5 px-2 rounded-lg text-[10px] bg-purple-50/80 hover:bg-purple-100 text-purple-700 border border-purple-200/60 font-medium transition-all active:scale-[0.98] disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer flex items-center justify-center gap-1"
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

      {showBuildConfirm && buildScope && (
        <BuildConfirmModal
          pendingCount={pendingCount}
          initialType={buildInitialType}
          collectionId={buildScope.id}
          collectionTitle={buildScope.title}
          platform={buildScope.platform}
          onClose={() => setShowBuildConfirm(false)}
          onConfirm={(selectedIds, contentType, scope, buildPlat) => {
            setShowBuildConfirm(false);
            handleBuild(selectedIds, contentType, scope, buildPlat, buildScope);
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

      {showExportModal && exportScope && (
        <ExportModal
          collectionId={exportScope.id}
          collectionTitle={exportScope.title}
          platform={exportScope.platform}
          doneCount={doneCount}
          onClose={closeExportModal}
          onExportStarted={(taskId, mode) => startExportPolling(taskId, mode)}
        />
      )}
    </div>
  );
}
