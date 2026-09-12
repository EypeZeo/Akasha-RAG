import { useState, useCallback, useEffect } from 'react';
import ChatPanel from '../components/ChatPanel';
import SettingsModal from '../components/SettingsModal';
import LoginModal from '../components/LoginModal';
import ActivityBar from '../components/ActivityBar';
import SourcesStudio from '../components/SourcesStudio';
import { useI18n } from '../i18n';
import * as api from '../api';
import { useWorkspaceStore } from '../store/workspace';
import {
  useLocalStorageSetting,
  COLLECTIONS_PER_PAGE_OPTIONS,
  VIDEOS_PER_PAGE_OPTIONS,
  ACTIVITY_BAR_POSITIONS,
  DEFAULT_COLLECTIONS_PER_PAGE,
  DEFAULT_VIDEOS_PER_PAGE,
  DEFAULT_ACTIVITY_BAR_POSITION,
} from '../utils/settings';

interface Props {
  onLogout: () => void;
  onAccountsChanged?: () => void;
}

export default function Workspace({ onLogout, onAccountsChanged }: Props) {
  const { t } = useI18n();
  const activeTab = useWorkspaceStore(s => s.activeTab);
  const setActiveTab = useWorkspaceStore(s => s.setActiveTab);
  const selectedCollectionId = useWorkspaceStore(s => s.selectedCollectionId);
  const selectedPlatform = useWorkspaceStore(s => s.selectedPlatform);
  const activeSessionId = useWorkspaceStore(s => s.activeSessionId);
  const setActiveSessionId = useWorkspaceStore(s => s.setActiveSessionId);
  const [activityBarPosition, setActivityBarPosition] = useLocalStorageSetting(
    'ui.activityBarPosition', ACTIVITY_BAR_POSITIONS, DEFAULT_ACTIVITY_BAR_POSITION,
  );
  const [statsRefreshKey, setStatsRefreshKey] = useState(0);
  const [loggedPlatforms, setLoggedPlatforms] = useState<api.PlatformInfo[]>([]);
  const [showSettings, setShowSettings] = useState(false);
  const [showLoginModal, setShowLoginModal] = useState(false);
  const [showLogoutConfirm, setShowLogoutConfirm] = useState(false);
  const [accountRefreshKey, setAccountRefreshKey] = useState(0);
  const [loginModalPlatform, setLoginModalPlatform] = useState<'douyin' | 'bilibili'>('douyin');
  const [logLevel, setLogLevel] = useState('INFO');
  const [availableLevels, setAvailableLevels] = useState<string[]>(['DEBUG', 'INFO', 'WARNING', 'ERROR', 'NONE']);
  const [collectionsPerPage, setCollectionsPerPage] = useLocalStorageSetting<number>(
    'ui.collectionsPerPage', COLLECTIONS_PER_PAGE_OPTIONS, DEFAULT_COLLECTIONS_PER_PAGE,
  );
  const [videosPerPage, setVideosPerPage] = useLocalStorageSetting<number>(
    'ui.videosPerPage', VIDEOS_PER_PAGE_OPTIONS, DEFAULT_VIDEOS_PER_PAGE,
  );

  const handleAccountsChanged = useCallback(() => {
    setAccountRefreshKey(key => key + 1);
    onAccountsChanged?.();
  }, [onAccountsChanged]);

  // Poll login status for detailed message
  useEffect(() => {
    let lastStatus = '';
    let timer: ReturnType<typeof setTimeout>;
    let cancelled = false;

    const tick = async () => {
      try {
        const pRes = await api.listPlatforms();
        if (pRes.success && pRes.platforms) {
          const dy = pRes.platforms.find(p => p.platform === 'douyin');
          const bili = pRes.platforms.find(p => p.platform === 'bilibili');
          setLoggedPlatforms([dy, bili].filter((item): item is api.PlatformInfo => Boolean(item?.is_logged_in)));

          if (dy?.status === 'logged_in' && lastStatus === 'syncing') {
            setStatsRefreshKey(k => k + 1);
          }
          lastStatus = dy?.status || '';
        } else {
          const s = await api.loginStatus();
          lastStatus = s.status;
        }
      } catch {}
      if (cancelled) return;
      const fast = lastStatus === 'syncing' || lastStatus === 'pending';
      timer = setTimeout(tick, fast ? 3000 : 30000);
    };

    tick();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [accountRefreshKey]);

  // Fetch initial log level
  useEffect(() => {
    api.getLogLevel().then(r => {
      if (r.success) {
        setLogLevel(r.current_level);
        if (r.available_levels) setAvailableLevels(r.available_levels);
      }
    }).catch(() => {});
  }, []);

  const handleLogLevelChange = async (newLevel: string) => {
    setLogLevel(newLevel);
    try {
      const res = await api.setLogLevel(newLevel);
      if (res.success) {
        setLogLevel(res.current_level);
      }
    } catch (e: any) {
      alert('更新日志级别失败: ' + e.message);
    }
  };

  const [cacheMb, setCacheMb] = useState<number | null>(null);
  const [cacheCleaning, setCacheCleaning] = useState(false);

  const fetchCacheStats = useCallback(async () => {
    try {
      const res = await api.getAudioCacheStats();
      if (res.success && res.stats) {
        setCacheMb(res.stats.total_mb);
      }
    } catch {}
  }, []);

  // 定期拉取音频缓存统计
  useEffect(() => {
    fetchCacheStats();
    const timer = setInterval(fetchCacheStats, 30000);
    return () => clearInterval(timer);
  }, [fetchCacheStats]);

  const handleCleanCache = async () => {
    if (cacheCleaning) return;
    setCacheCleaning(true);
    try {
      const res = await api.cleanAudioCache();
      if (res.success) {
        const { deleted_files, freed_mb } = res.result;
        alert(t('cacheCleanSuccess', { deleted: deleted_files, freed: freed_mb }));
        fetchCacheStats();
      }
    } catch (e: any) {
      alert('清理缓存失败: ' + e.message);
    } finally {
      setCacheCleaning(false);
    }
  };

  const handleOpenLogs = async () => {
    try {
      const r = await api.openLocalFolder('logs');
      if (!r.success) alert(r.message || '打开日志目录失败');
    } catch (e: any) {
      alert('打开日志目录失败: ' + e.message);
    }
  };

  return (
    <div className="h-screen flex flex-col bg-[var(--color-bg)]">
      {/* Top Bar */}
      <header className="flex items-center justify-between px-6 py-3 bg-white/85 backdrop-blur-md border-b border-[var(--color-border)] shadow-xs flex-shrink-0">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-2xl flex items-center justify-center p-1 bg-white/70 border border-black/5 shadow-md shadow-accent/15 overflow-hidden">
            <img src="/akasha-mark.svg" alt="Akasha-RAG" className="w-full h-full object-contain" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h1 className="font-display text-base font-bold text-[var(--color-ink)]">{t('appTitle')}</h1>
              <span className="px-1.5 py-0.5 rounded-md text-[10px] font-semibold bg-accent-light text-accent border border-accent/20">v{__APP_VERSION__}</span>
            </div>
            <span className="text-[10px] text-[var(--color-ink-muted)] tracking-wider">{t('appSubtitle')}</span>
          </div>
        </div>

        {/* System Toolbar & Controls */}
        <div className="flex items-center gap-3">
          {/* Settings Button (SVG Gear Icon) */}
          <button
            onClick={() => setShowSettings(true)}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-xl bg-white/90 border border-[var(--color-border)] hover:border-accent/50 text-xs text-[var(--color-ink-soft)] hover:text-accent shadow-2xs hover:shadow-xs transition-all cursor-pointer group"
            title={t('settingsTitle')}
          >
            <svg
              className="w-4 h-4 text-[var(--color-ink-muted)] group-hover:text-accent group-hover:rotate-45 transition-all duration-300"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth={2}
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z" />
              <circle cx="12" cy="12" r="3" />
            </svg>
            <span className="font-semibold">{t('settings')}</span>
          </button>

          {/* Status Badge */}
          <span
            className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium max-w-md ${
              loggedPlatforms.length ? 'bg-[rgba(91,140,90,0.12)] text-[var(--color-success)]' : 'bg-black/5 text-[var(--color-ink-muted)]'
            }`}
          >
            <span className={`w-2 h-2 rounded-full flex-shrink-0 ${loggedPlatforms.length ? 'bg-[var(--color-success)] animate-pulse' : 'bg-gray-400'}`} />
            {loggedPlatforms.length === 0 ? (
              <span>{t('notLoggedIn')}</span>
            ) : (
              <>
                <span>已登录平台 {loggedPlatforms.length}/2</span>
                {loggedPlatforms.map(platform => (
                  <img
                    key={platform.platform}
                    src={platform.platform === 'bilibili' ? '/platform-icons/bilibili.svg' : '/platform-icons/douyin.svg'}
                    alt={platform.name}
                    className="w-3.5 h-3.5 object-contain"
                  />
                ))}
              </>
            )}
          </span>

          {/* Logout Button (SVG Power Icon) */}
          <button
            onClick={() => setShowLogoutConfirm(true)}
            className="w-8 h-8 rounded-xl border border-[var(--color-border)] bg-white/80 flex items-center justify-center hover:border-red-300 hover:text-red-500 hover:bg-red-50/50 transition-all text-xs text-[var(--color-ink-muted)] shadow-2xs cursor-pointer group"
            title={t('logoutTooltip')}
          >
            <svg className="w-4 h-4 text-[var(--color-ink-muted)] group-hover:text-red-500 transition-colors" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.2} strokeLinecap="round" strokeLinejoin="round">
              <path d="M18.36 6.64a9 9 0 1 1-12.73 0" />
              <line x1="12" y1="2" x2="12" y2="12" />
            </svg>
          </button>
        </div>
      </header>

      {/* Main Area — Activity bar + mutually-exclusive tab panes.
          Both panes stay mounted; visibility is toggled with `hidden` so a tab
          switch never unmounts the chat transcript or the sources pagination. */}
      <div
        className={`flex-1 flex min-h-0 ${
          activityBarPosition === 'left' ? 'flex-row'
            : activityBarPosition === 'right' ? 'flex-row-reverse'
            : activityBarPosition === 'top' ? 'flex-col'
            : 'flex-col-reverse'
        }`}
      >
        <ActivityBar
          position={activityBarPosition}
          activeTab={activeTab}
          onTabChange={setActiveTab}
          onOpenSettings={() => setShowSettings(true)}
          loggedPlatforms={loggedPlatforms}
        />

        <div className="flex-1 flex min-h-0 relative">
          <div
            id="workspace-pane-sources"
            role="tabpanel"
            aria-label={t('tabSources')}
            hidden={activeTab !== 'sources'}
            className="absolute inset-0"
          >
            <SourcesStudio
              onBuildDone={() => setStatsRefreshKey(k => k + 1)}
              statsRefreshKey={statsRefreshKey}
              collectionsPerPage={collectionsPerPage}
              videosPerPage={videosPerPage}
              onOpenSettings={() => setShowSettings(true)}
            />
          </div>

          <main
            id="workspace-pane-chat"
            role="tabpanel"
            aria-label={t('tabChat')}
            hidden={activeTab !== 'chat'}
            className="absolute inset-0 min-w-0"
          >
            <ChatPanel
              collectionId={selectedCollectionId}
              platform={selectedPlatform}
              statsRefreshKey={statsRefreshKey}
              activeSessionId={activeSessionId}
              onSelectSession={setActiveSessionId}
              active={activeTab === 'chat'}
              onOpenSettings={() => setShowSettings(true)}
            />
          </main>
        </div>
      </div>

      {/* Settings Modal */}
      <SettingsModal
        isOpen={showSettings}
        onClose={() => setShowSettings(false)}
        logLevel={logLevel}
        availableLevels={availableLevels}
        onLogLevelChange={handleLogLevelChange}
        collectionsPerPage={collectionsPerPage}
        videosPerPage={videosPerPage}
        onCollectionsPerPageChange={setCollectionsPerPage}
        onVideosPerPageChange={setVideosPerPage}
        activityBarPosition={activityBarPosition}
        onActivityBarPositionChange={setActivityBarPosition}
        cacheMb={cacheMb}
        cacheCleaning={cacheCleaning}
        onCleanCache={handleCleanCache}
        onOpenLogs={handleOpenLogs}
        onOpenLoginModal={(plat) => {
          setLoginModalPlatform(plat);
          setShowLoginModal(true);
        }}
        onAccountsChanged={handleAccountsChanged}
      />
      {showLogoutConfirm && (
        <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/45 backdrop-blur-xs p-4" onClick={() => setShowLogoutConfirm(false)}>
          <div className="w-full max-w-sm rounded-2xl bg-white p-6 shadow-2xl border border-[var(--color-border)]" onClick={event => event.stopPropagation()}>
            <h2 className="text-base font-bold text-[var(--color-ink)]">退出全部平台？</h2>
            <p className="mt-2 text-xs leading-5 text-[var(--color-ink-soft)]">将退出当前已登录的平台，并清除本地授权凭证。收藏与已入库知识不会删除。</p>
            <div className="mt-5 flex justify-end gap-2">
              <button onClick={() => setShowLogoutConfirm(false)} className="px-3 py-1.5 text-xs rounded-lg border border-[var(--color-border)] text-[var(--color-ink-soft)]">{t('cancel')}</button>
              <button onClick={() => { setShowLogoutConfirm(false); onLogout(); }} className="px-3 py-1.5 text-xs rounded-lg bg-red-500 text-white">退出全部</button>
            </div>
          </div>
        </div>
      )}

      {/* Login Modal (triggered from settings) */}
      {showLoginModal && (
        <LoginModal
          onClose={() => {
            setShowLoginModal(false);
            handleAccountsChanged();
          }}
          onSuccess={() => {
            setShowLoginModal(false);
            setStatsRefreshKey(k => k + 1);
            handleAccountsChanged();
          }}
          initialPlatform={loginModalPlatform}
        />
      )}
    </div>
  );
}
