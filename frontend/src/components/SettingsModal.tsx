import { useState, useEffect, useCallback, useRef } from 'react';
import { useI18n, SupportedLang } from '../i18n';
import * as api from '../api';
import {
  COLLECTIONS_PER_PAGE_OPTIONS,
  VIDEOS_PER_PAGE_OPTIONS,
  ACTIVITY_BAR_POSITIONS,
  type ActivityBarPosition,
} from '../utils/settings';

interface SettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
  logLevel: string;
  availableLevels: string[];
  onLogLevelChange: (lvl: string) => Promise<void>;
  collectionsPerPage: number;
  videosPerPage: number;
  onCollectionsPerPageChange: (n: number) => void;
  onVideosPerPageChange: (n: number) => void;
  activityBarPosition: ActivityBarPosition;
  onActivityBarPositionChange: (p: ActivityBarPosition) => void;
  cacheMb: number | null;
  cacheCleaning: boolean;
  onCleanCache: () => Promise<void>;
  onOpenLogs: () => Promise<void>;
  onOpenLoginModal?: (platform: 'douyin' | 'bilibili') => void;
  onAccountsChanged?: () => void;
}

/** Collapsible section wrapper for settings that don't need to be visible by default. */
function Disclosure({ title, description, icon, children }: {
  title: string;
  description: string;
  icon: string;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className="rounded-xl border border-[var(--color-border)] bg-white/60 overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        className="w-full flex items-center justify-between gap-2 p-4 text-left cursor-pointer"
      >
        <div className="flex items-center gap-2">
          <span className="text-base">{icon}</span>
          <div>
            <h3 className="text-sm font-bold text-[var(--color-ink)]">{title}</h3>
            <p className="text-xs text-[var(--color-ink-muted)]">{description}</p>
          </div>
        </div>
        <svg
          className={`w-4 h-4 text-[var(--color-ink-muted)] shrink-0 transition-transform ${open ? 'rotate-180' : ''}`}
          viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round"
        >
          <path d="M6 9l6 6 6-6" />
        </svg>
      </button>
      {open && <div className="px-4 pb-4 space-y-3">{children}</div>}
    </div>
  );
}

function PlatformAvatar({ platform, avatarUrl }: { platform: api.PlatformKind; avatarUrl?: string }) {
  const [failed, setFailed] = useState(false);
  useEffect(() => setFailed(false), [avatarUrl]);
  const fallback = platform === 'bilibili'
    ? '/platform-icons/bilibili.svg'
    : '/platform-icons/douyin.svg';
  return (
    <img
      src={avatarUrl && !failed ? avatarUrl : fallback}
      alt=""
      referrerPolicy="no-referrer"
      onError={() => setFailed(true)}
      className="w-8 h-8 rounded-full border border-pink-300 object-cover bg-white p-1"
    />
  );
}

export default function SettingsModal({
  isOpen,
  onClose,
  logLevel,
  availableLevels,
  onLogLevelChange,
  collectionsPerPage,
  videosPerPage,
  onCollectionsPerPageChange,
  onVideosPerPageChange,
  activityBarPosition,
  onActivityBarPositionChange,
  cacheMb,
  cacheCleaning,
  onCleanCache,
  onOpenLogs,
  onOpenLoginModal,
  onAccountsChanged,
}: SettingsModalProps) {
  const { lang, setLang, t, languages } = useI18n();
  const [openingLogs, setOpeningLogs] = useState(false);
  const [platforms, setPlatforms] = useState<api.PlatformInfo[]>([]);
  const [loadingPlatforms, setLoadingPlatforms] = useState(false);
  const [loggingOutPlatform, setLoggingOutPlatform] = useState<api.PlatformKind | null>(null);
  const [justRefreshed, setJustRefreshed] = useState(false);
  const refreshTimerRef = useRef<any>(null);

  // ---- API Keys: DashScope ----
  const [dashscopeMasked, setDashscopeMasked] = useState('');
  const [dashscopeConfigured, setDashscopeConfigured] = useState(false);
  const [dashscopeInput, setDashscopeInput] = useState('');
  const [savingDashscope, setSavingDashscope] = useState(false);

  // ---- API Keys: chat providers ----
  const [chatProviders, setChatProviders] = useState<api.ChatProvider[]>([]);
  const [loadingProviders, setLoadingProviders] = useState(false);
  const emptyProviderForm = { display_name: '', protocol: 'openai' as 'openai' | 'anthropic', base_url: '', api_key: '', model_id: '' };
  const [editingProviderId, setEditingProviderId] = useState<string | null>(null);
  const [showProviderForm, setShowProviderForm] = useState(false);
  const [providerForm, setProviderForm] = useState(emptyProviderForm);
  const [savingProvider, setSavingProvider] = useState(false);

  const fetchApiSettings = useCallback(async () => {
    setLoadingProviders(true);
    try {
      const [keyRes, providersRes] = await Promise.all([api.getDashscopeKey(), api.listChatProviders()]);
      if (keyRes.success) {
        setDashscopeConfigured(keyRes.configured);
        setDashscopeMasked(keyRes.api_key_masked);
      }
      if (providersRes.success) {
        setChatProviders(providersRes.providers);
      }
    } catch {} finally {
      setLoadingProviders(false);
    }
  }, []);

  const handleSaveDashscopeKey = async () => {
    if (!dashscopeInput.trim()) return;
    setSavingDashscope(true);
    try {
      await api.setDashscopeKey(dashscopeInput.trim());
      setDashscopeInput('');
      await fetchApiSettings();
    } catch (e: any) {
      alert('保存失败: ' + e.message);
    } finally {
      setSavingDashscope(false);
    }
  };

  const openAddProvider = () => {
    setEditingProviderId(null);
    setProviderForm(emptyProviderForm);
    setShowProviderForm(true);
  };

  const openEditProvider = (p: api.ChatProvider) => {
    setEditingProviderId(p.id);
    setProviderForm({ display_name: p.display_name, protocol: p.protocol, base_url: p.base_url, api_key: '', model_id: p.model_id });
    setShowProviderForm(true);
  };

  const handleSaveProvider = async () => {
    if (!providerForm.display_name.trim() || !providerForm.base_url.trim() || !providerForm.model_id.trim()) return;
    if (!editingProviderId && !providerForm.api_key.trim()) return;
    setSavingProvider(true);
    try {
      await api.upsertChatProvider({
        id: editingProviderId ?? undefined,
        display_name: providerForm.display_name,
        protocol: providerForm.protocol,
        base_url: providerForm.base_url,
        api_key: providerForm.api_key || undefined,
        model_id: providerForm.model_id,
      });
      setShowProviderForm(false);
      await fetchApiSettings();
    } catch (e: any) {
      alert('保存失败: ' + e.message);
    } finally {
      setSavingProvider(false);
    }
  };

  const handleDeleteProvider = async (id: string) => {
    if (!confirm(t('chatProviderDeleteConfirm'))) return;
    await api.deleteChatProvider(id);
    await fetchApiSettings();
  };

  const handleActivateProvider = async (id: string) => {
    await api.activateChatProvider(id);
    await fetchApiSettings();
  };

  const fetchPlatforms = useCallback(async () => {
    setLoadingPlatforms(true);
    try {
      const res = await api.listPlatforms();
      if (res.success && res.platforms) {
        setPlatforms(res.platforms);
      }
      setJustRefreshed(true);
      if (refreshTimerRef.current) clearTimeout(refreshTimerRef.current);
      refreshTimerRef.current = setTimeout(() => {
        setJustRefreshed(false);
      }, 2500);
    } catch {} finally {
      setLoadingPlatforms(false);
    }
  }, []);

  useEffect(() => {
    if (isOpen) {
      fetchPlatforms();
      fetchApiSettings();
    }
  }, [isOpen, fetchPlatforms, fetchApiSettings]);

  const handleDouyinLogout = async () => {
    setLoggingOutPlatform('douyin');
    try {
      const result = await api.logout();
      if (!result.success) throw new Error(result.message || '退出失败');
      setPlatforms(prev => prev.map(item => item.platform === 'douyin'
        ? { ...item, is_logged_in: false, status: 'idle', nickname: '', avatar_url: '' }
        : item));
      onAccountsChanged?.();
    } catch (e: any) {
      alert('退出抖音登录失败: ' + e.message);
    } finally {
      setLoggingOutPlatform(null);
    }
  };

  const handleBilibiliLogout = async () => {
    setLoggingOutPlatform('bilibili');
    try {
      const result = await api.bilibiliLogout();
      if (!result.success) throw new Error(result.message || '退出失败');
      setPlatforms(prev => prev.map(item => item.platform === 'bilibili'
        ? { ...item, is_logged_in: false, status: 'idle', nickname: '', avatar_url: '' }
        : item));
      onAccountsChanged?.();
    } catch (e: any) {
      alert('退出B站登录失败: ' + e.message);
    } finally {
      setLoggingOutPlatform(null);
    }
  };

  if (!isOpen) return null;

  const handleOpenLogsDir = async () => {
    setOpeningLogs(true);
    try {
      await onOpenLogs();
    } finally {
      setOpeningLogs(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/45 backdrop-blur-xs p-4 animate-fade-in"
      onClick={e => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="bg-white rounded-2xl shadow-2xl border border-[var(--color-border)] w-full max-w-xl max-h-[90vh] flex flex-col overflow-hidden animate-scale-up">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-[var(--color-border)] bg-gray-50/50 flex-shrink-0">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-xl bg-accent/10 flex items-center justify-center text-accent">
              {/* Settings Gear SVG */}
              <svg className="w-5 h-5 animate-spin-slow" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
                <path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z" />
                <circle cx="12" cy="12" r="3" />
              </svg>
            </div>
            <div>
              <h2 className="text-base font-bold text-[var(--color-ink)]">{t('settingsTitle')}</h2>
              <span className="text-[11px] text-[var(--color-ink-muted)]">Akasha-RAG</span>
            </div>
          </div>
          <button
            onClick={onClose}
            className="w-8 h-8 rounded-lg hover:bg-black/5 text-[var(--color-ink-muted)] hover:text-[var(--color-ink)] flex items-center justify-center transition-colors cursor-pointer"
            title={t('close')}
          >
            <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </div>

        {/* Body */}
        <div className="p-6 overflow-y-auto space-y-6 subtle-scrollbar">
          {/* Section 0: Platform Accounts */}
          <div className="rounded-xl border border-[var(--color-border)] p-4 bg-white/60 space-y-3">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span className="text-base">📱</span>
                <div>
                  <h3 className="text-sm font-bold text-[var(--color-ink)]">{t('accountsTitle')}</h3>
                  <p className="text-xs text-[var(--color-ink-muted)]">{t('accountsDesc')}</p>
                </div>
              </div>
              <button
                type="button"
                onClick={fetchPlatforms}
                disabled={loadingPlatforms}
                className={`group relative overflow-hidden flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium transition-all duration-300 ${
                  loadingPlatforms
                    ? 'bg-accent/12 text-accent border border-accent/40 animate-sync-glow cursor-wait select-none'
                    : justRefreshed
                    ? 'bg-green-50 text-green-700 border border-green-300 shadow-[0_0_10px_rgba(34,197,94,0.25)]'
                    : 'text-[var(--color-ink-soft)] bg-black/4 hover:bg-black/7 hover:text-[var(--color-ink)] border border-transparent shadow-2xs cursor-pointer active:scale-95'
                }`}
                title="刷新账号状态"
              >
                {loadingPlatforms && (
                  <span
                    className="absolute inset-0 pointer-events-none bg-gradient-to-r from-transparent via-white/40 to-transparent animate-sync-shimmer"
                    aria-hidden="true"
                  />
                )}
                {loadingPlatforms ? (
                  <>
                    <svg className="w-3.5 h-3.5 animate-spin text-accent shrink-0" viewBox="0 0 24 24" fill="none">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" />
                      <path className="opacity-90" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                    </svg>
                    <span className="font-semibold tracking-wide">检查中...</span>
                    <span className="relative flex h-2 w-2 ml-0.5">
                      <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-accent opacity-75" />
                      <span className="relative inline-flex rounded-full h-2 w-2 bg-accent" />
                    </span>
                  </>
                ) : justRefreshed ? (
                  <>
                    <svg className="w-3.5 h-3.5 text-green-600 shrink-0" viewBox="0 0 20 20" fill="currentColor">
                      <path fillRule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clipRule="evenodd" />
                    </svg>
                    <span className="font-semibold text-green-700">状态已更新</span>
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
                    <span>刷新账号状态</span>
                  </>
                )}
              </button>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 pt-1">
              {/* Douyin Account */}
              {(() => {
                const dy = platforms.find(p => p.platform === 'douyin');
                const isLogged = dy?.is_logged_in ?? false;
                return (
                  <div className="flex flex-col justify-between p-3 rounded-xl border border-[var(--color-border)] bg-white shadow-2xs gap-3">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <PlatformAvatar platform="douyin" avatarUrl={dy?.avatar_url} />
                        <div>
                          <div className="text-xs font-bold text-[var(--color-ink)]">{dy?.nickname || t('douyinAccount')}</div>
                          <span className={`inline-flex items-center gap-1 text-[10px] font-medium ${isLogged ? 'text-green-600' : 'text-gray-400'}`}>
                            <span className={`w-1.5 h-1.5 rounded-full ${isLogged ? 'bg-green-500' : 'bg-gray-300'}`} />
                            {isLogged ? t('loggedIn') : t('notLoggedIn')}
                          </span>
                        </div>
                      </div>
                    </div>
                    <div className="flex items-center justify-end">
                      {isLogged ? (
                        <button
                          type="button"
                          onClick={handleDouyinLogout}
                          disabled={loggingOutPlatform !== null}
                          className={`flex items-center gap-1.5 px-3 py-1 text-xs font-medium rounded-lg border transition-all ${
                            loggingOutPlatform === 'douyin'
                              ? 'bg-red-50 text-red-600 border-red-300 shadow-[0_0_8px_rgba(239,68,68,0.25)] cursor-wait select-none'
                              : 'text-red-600 hover:bg-red-50/80 border-red-200 hover:border-red-300 cursor-pointer active:scale-95'
                          } disabled:opacity-50`}
                        >
                          {loggingOutPlatform === 'douyin' ? (
                            <>
                              <svg className="w-3 h-3 animate-spin text-red-500 shrink-0" viewBox="0 0 24 24" fill="none">
                                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" />
                                <path className="opacity-90" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                              </svg>
                              <span>正在退出…</span>
                            </>
                          ) : (
                            t('logout')
                          )}
                        </button>
                      ) : (
                        <button
                          type="button"
                          onClick={() => {
                            onClose();
                            onOpenLoginModal?.('douyin');
                          }}
                          className="px-3 py-1 text-xs text-accent font-semibold bg-accent-light hover:bg-accent/15 rounded-lg border border-accent/30 transition-colors cursor-pointer"
                        >
                          {t('startLogin')}
                        </button>
                      )}
                    </div>
                  </div>
                );
              })()}

              {/* Bilibili Account */}
              {(() => {
                const bili = platforms.find(p => p.platform === 'bilibili');
                const isLogged = bili?.is_logged_in ?? false;
                return (
                  <div className="flex flex-col justify-between p-3 rounded-xl border border-[var(--color-border)] bg-white shadow-2xs gap-3">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <PlatformAvatar platform="bilibili" avatarUrl={bili?.avatar_url} />
                        <div className="min-w-0 flex-1">
                          <div className="text-xs font-bold text-[var(--color-ink)] truncate">
                            {bili?.nickname ? bili.nickname : t('bilibiliAccount')}
                          </div>
                          <span className={`inline-flex items-center gap-1 text-[10px] font-medium ${isLogged ? 'text-pink-600' : 'text-gray-400'}`}>
                            <span className={`w-1.5 h-1.5 rounded-full ${isLogged ? 'bg-pink-500' : 'bg-gray-300'}`} />
                            {isLogged ? t('loggedIn') : t('notLoggedIn')}
                          </span>
                        </div>
                      </div>
                    </div>
                    <div className="flex items-center justify-end">
                      {isLogged ? (
                        <button
                          type="button"
                          onClick={handleBilibiliLogout}
                          disabled={loggingOutPlatform !== null}
                          className={`flex items-center gap-1.5 px-3 py-1 text-xs font-medium rounded-lg border transition-all ${
                            loggingOutPlatform === 'bilibili'
                              ? 'bg-red-50 text-red-600 border-red-300 shadow-[0_0_8px_rgba(239,68,68,0.25)] cursor-wait select-none'
                              : 'text-red-600 hover:bg-red-50/80 border-red-200 hover:border-red-300 cursor-pointer active:scale-95'
                          } disabled:opacity-50`}
                        >
                          {loggingOutPlatform === 'bilibili' ? (
                            <>
                              <svg className="w-3 h-3 animate-spin text-red-500 shrink-0" viewBox="0 0 24 24" fill="none">
                                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" />
                                <path className="opacity-90" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                              </svg>
                              <span>正在退出…</span>
                            </>
                          ) : (
                            t('logout')
                          )}
                        </button>
                      ) : (
                        <button
                          type="button"
                          onClick={() => {
                            onClose();
                            onOpenLoginModal?.('bilibili');
                          }}
                          className="px-3 py-1 text-xs text-pink-600 font-semibold bg-pink-50 hover:bg-pink-100 rounded-lg border border-pink-200 transition-colors cursor-pointer"
                        >
                          {t('loginBilibili')}
                        </button>
                      )}
                    </div>
                  </div>
                );
              })()}
            </div>
          </div>

          {/* Section: API Keys (DashScope) */}
          <div className="rounded-xl border border-[var(--color-border)] p-4 bg-white/60 space-y-3">
            <div className="flex items-center gap-2">
              <span className="text-base">🔑</span>
              <div>
                <h3 className="text-sm font-bold text-[var(--color-ink)]">{t('dashscopeKeyTitle')}</h3>
                <p className="text-xs text-[var(--color-ink-muted)]">{t('dashscopeKeyDesc')}</p>
              </div>
            </div>
            <div className="flex items-center gap-2 pt-1">
              <input
                type="password"
                value={dashscopeInput}
                onChange={e => setDashscopeInput(e.target.value)}
                placeholder={dashscopeConfigured ? dashscopeMasked : t('dashscopeKeyPlaceholder')}
                className="flex-1 min-w-0 bg-white border border-[var(--color-border)] rounded-lg text-xs px-3 py-2 focus:outline-none focus:border-accent"
              />
              <button
                onClick={handleSaveDashscopeKey}
                disabled={savingDashscope || !dashscopeInput.trim()}
                className="flex-shrink-0 px-3.5 py-2 rounded-lg bg-gradient-to-r from-accent to-accent-hover text-white text-xs font-semibold shadow-xs disabled:opacity-40 cursor-pointer disabled:cursor-not-allowed"
              >
                {savingDashscope ? t('saving') : t('save')}
              </button>
            </div>
            <p className="text-[11px] text-[var(--color-ink-muted)]">{t('apiKeyLocalOnlyNote')}</p>
          </div>

          {/* Section: Chat providers (multi-provider, saved list + switch) */}
          <div className="rounded-xl border border-[var(--color-border)] p-4 bg-white/60 space-y-3">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span className="text-base">🤖</span>
                <div>
                  <h3 className="text-sm font-bold text-[var(--color-ink)]">{t('chatProvidersTitle')}</h3>
                  <p className="text-xs text-[var(--color-ink-muted)]">{t('chatProvidersDesc')}</p>
                </div>
              </div>
              {!showProviderForm && (
                <button
                  onClick={openAddProvider}
                  className="px-3 py-1.5 text-xs font-medium rounded-lg border border-accent/30 text-accent bg-accent-light hover:bg-accent/15 transition-colors cursor-pointer flex-shrink-0"
                >
                  + {t('chatProviderAddBtn')}
                </button>
              )}
            </div>

            {!showProviderForm && (
              <div className="space-y-2 pt-1">
                {chatProviders.length === 0 && !loadingProviders && (
                  <p className="text-xs text-[var(--color-ink-muted)] italic">{t('chatProvidersEmpty')}</p>
                )}
                {chatProviders.map(p => (
                  <div key={p.id} className="flex items-center justify-between gap-2 p-2.5 rounded-lg border border-[var(--color-border)] bg-white">
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-1.5">
                        <span className="text-xs font-bold text-[var(--color-ink)] truncate">{p.display_name}</span>
                        <span className="text-[10px] px-1.5 py-0.5 rounded bg-black/5 text-[var(--color-ink-muted)] flex-shrink-0">
                          {p.protocol === 'anthropic' ? t('protocolAnthropic') : t('protocolOpenAI')}
                        </span>
                        {p.is_active && (
                          <span className="text-[10px] px-1.5 py-0.5 rounded bg-green-100 text-green-700 flex-shrink-0">{t('chatProviderActiveLabel')}</span>
                        )}
                      </div>
                      <div className="text-[10px] text-[var(--color-ink-muted)] truncate">{p.model_id} · {p.base_url} · {p.api_key_masked}</div>
                    </div>
                    <div className="flex items-center gap-1 flex-shrink-0">
                      {!p.is_active && (
                        <button onClick={() => handleActivateProvider(p.id)} className="px-2 py-1 text-[10px] rounded-md border border-[var(--color-border)] hover:border-accent/40 text-[var(--color-ink-soft)] cursor-pointer">
                          {t('chatProviderActivateBtn')}
                        </button>
                      )}
                      <button onClick={() => openEditProvider(p)} className="px-2 py-1 text-[10px] rounded-md border border-[var(--color-border)] hover:border-accent/40 text-[var(--color-ink-soft)] cursor-pointer">
                        {t('chatProviderEditBtn')}
                      </button>
                      <button onClick={() => handleDeleteProvider(p.id)} className="px-2 py-1 text-[10px] rounded-md border border-red-200 hover:bg-red-50 text-red-600 cursor-pointer">
                        {t('chatProviderDeleteBtn')}
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}

            {showProviderForm && (
              <div className="space-y-2.5 pt-1">
                <input
                  value={providerForm.display_name}
                  onChange={e => setProviderForm(f => ({ ...f, display_name: e.target.value }))}
                  placeholder={t('chatProviderFormDisplayName')}
                  className="w-full bg-white border border-[var(--color-border)] rounded-lg text-xs px-3 py-2 focus:outline-none focus:border-accent"
                />
                <div className="grid grid-cols-2 gap-2">
                  <select
                    value={providerForm.protocol}
                    onChange={e => setProviderForm(f => ({ ...f, protocol: e.target.value as 'openai' | 'anthropic' }))}
                    className="bg-white border border-[var(--color-border)] rounded-lg text-xs px-2 py-2 focus:outline-none focus:border-accent"
                  >
                    <option value="openai">{t('protocolOpenAI')}</option>
                    <option value="anthropic">{t('protocolAnthropic')}</option>
                  </select>
                  <input
                    value={providerForm.model_id}
                    onChange={e => setProviderForm(f => ({ ...f, model_id: e.target.value }))}
                    placeholder={t('chatProviderFormModelId')}
                    className="bg-white border border-[var(--color-border)] rounded-lg text-xs px-3 py-2 focus:outline-none focus:border-accent"
                  />
                </div>
                <input
                  value={providerForm.base_url}
                  onChange={e => setProviderForm(f => ({ ...f, base_url: e.target.value }))}
                  placeholder={t('chatProviderFormBaseUrl')}
                  className="w-full bg-white border border-[var(--color-border)] rounded-lg text-xs px-3 py-2 focus:outline-none focus:border-accent"
                />
                <input
                  type="password"
                  value={providerForm.api_key}
                  onChange={e => setProviderForm(f => ({ ...f, api_key: e.target.value }))}
                  placeholder={editingProviderId ? t('chatProviderFormApiKeyEditHint') : t('chatProviderFormApiKey')}
                  className="w-full bg-white border border-[var(--color-border)] rounded-lg text-xs px-3 py-2 focus:outline-none focus:border-accent"
                />
                <p className="text-[11px] text-[var(--color-ink-muted)]">{t('apiKeyLocalOnlyNote')}</p>
                <div className="flex justify-end gap-2 pt-1">
                  <button onClick={() => setShowProviderForm(false)} className="px-3 py-1.5 text-xs rounded-lg border border-[var(--color-border)] text-[var(--color-ink-soft)] cursor-pointer">
                    {t('chatProviderFormCancel')}
                  </button>
                  <button
                    onClick={handleSaveProvider}
                    disabled={savingProvider}
                    className="px-3 py-1.5 text-xs rounded-lg bg-gradient-to-r from-accent to-accent-hover text-white font-semibold disabled:opacity-40 cursor-pointer disabled:cursor-not-allowed"
                  >
                    {savingProvider ? t('saving') : t('chatProviderFormSave')}
                  </button>
                </div>
              </div>
            )}
          </div>

          {/* Section 1: Interface Language */}
          <div className="rounded-xl border border-[var(--color-border)] p-4 bg-white/60 space-y-3">
            <div className="flex items-center gap-2">
              <span className="text-base">🌐</span>
              <div>
                <h3 className="text-sm font-bold text-[var(--color-ink)]">{t('languageTitle')}</h3>
                <p className="text-xs text-[var(--color-ink-muted)]">{t('languageDesc')}</p>
              </div>
            </div>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 pt-1">
              {languages.map(item => {
                const isActive = lang === item.code;
                return (
                  <button
                    key={item.code}
                    onClick={() => setLang(item.code as SupportedLang)}
                    className={`px-3 py-2 rounded-xl text-xs font-medium border text-center transition-all cursor-pointer ${
                      isActive
                        ? 'bg-accent-light text-accent border-accent font-semibold shadow-xs'
                        : 'bg-white text-[var(--color-ink-soft)] border-[var(--color-border)] hover:border-accent/40 hover:bg-black/[0.01]'
                    }`}
                  >
                    <div>{item.label}</div>
                    <div className="text-[10px] text-[var(--color-ink-muted)] opacity-75">{item.name}</div>
                  </button>
                );
              })}
            </div>
          </div>

          {/* Section 1b: Interface & Pagination */}
          <div className="rounded-xl border border-[var(--color-border)] p-4 bg-white/60 space-y-3">
            <div className="flex items-center gap-2">
              <span className="text-base">📑</span>
              <div>
                <h3 className="text-sm font-bold text-[var(--color-ink)]">{t('uiPagingTitle')}</h3>
                <p className="text-xs text-[var(--color-ink-muted)]">{t('uiPagingDesc')}</p>
              </div>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 pt-1">
              <label className="flex items-center justify-between gap-2 text-xs text-[var(--color-ink-soft)]">
                <span>{t('collectionsPerPageLabel')}</span>
                <select
                  value={collectionsPerPage}
                  onChange={e => onCollectionsPerPageChange(Number(e.target.value))}
                  className="bg-white border border-[var(--color-border)] rounded-lg text-xs px-2 py-1 focus:outline-none focus:border-accent"
                >
                  {COLLECTIONS_PER_PAGE_OPTIONS.map(n => (
                    <option key={n} value={n}>
                      {n === 0 ? t('perPageAll') : t('perPage', { count: n })}
                    </option>
                  ))}
                </select>
              </label>
              <label className="flex items-center justify-between gap-2 text-xs text-[var(--color-ink-soft)]">
                <span>{t('videosPerPageLabel')}</span>
                <select
                  value={videosPerPage}
                  onChange={e => onVideosPerPageChange(Number(e.target.value))}
                  className="bg-white border border-[var(--color-border)] rounded-lg text-xs px-2 py-1 focus:outline-none focus:border-accent"
                >
                  {VIDEOS_PER_PAGE_OPTIONS.map(n => (
                    <option key={n} value={n}>{t('perPage', { count: n })}</option>
                  ))}
                </select>
              </label>
              <label className="flex items-center justify-between gap-2 text-xs text-[var(--color-ink-soft)] sm:col-span-2">
                <span>{t('activityBarPositionLabel')}</span>
                <select
                  value={activityBarPosition}
                  onChange={e => onActivityBarPositionChange(e.target.value as ActivityBarPosition)}
                  className="bg-white border border-[var(--color-border)] rounded-lg text-xs px-2 py-1 focus:outline-none focus:border-accent"
                >
                  {ACTIVITY_BAR_POSITIONS.map(p => (
                    <option key={p} value={p}>{t(`activityBarPos_${p}`)}</option>
                  ))}
                </select>
              </label>
            </div>
          </div>

          {/* Advanced settings: log level / log directory / cache — collapsed by default */}
          <Disclosure title={t('advancedSettingsTitle')} description={t('advancedSettingsDesc')} icon="⚙️">
            {/* Console Log Level */}
            <div className="rounded-lg border border-[var(--color-border)] p-3 bg-white space-y-3">
              <h4 className="text-xs font-bold text-[var(--color-ink)]">{t('logLevelTitle')}</h4>
              <div className="flex flex-wrap gap-2">
                {availableLevels.map(lvl => {
                  const isSelected = logLevel === lvl;
                  return (
                    <button
                      key={lvl}
                      onClick={() => onLogLevelChange(lvl)}
                      className={`px-3 py-1.5 rounded-xl text-xs font-mono font-bold border transition-all cursor-pointer ${
                        isSelected
                          ? 'bg-accent text-white border-accent shadow-xs'
                          : 'bg-white text-[var(--color-ink-soft)] border-[var(--color-border)] hover:border-accent/40 hover:bg-black/[0.01]'
                      }`}
                    >
                      {lvl}
                    </button>
                  );
                })}
              </div>
            </div>

            {/* Local Logs Directory */}
            <div className="rounded-lg border border-[var(--color-border)] p-3 bg-white flex items-center justify-between gap-4">
              <h4 className="text-xs font-bold text-[var(--color-ink)]">{t('logDirTitle')}</h4>
              <button
                onClick={handleOpenLogsDir}
                disabled={openingLogs}
                className={`flex-shrink-0 flex items-center gap-1.5 px-3.5 py-2 rounded-xl border text-xs font-medium transition-all ${
                  openingLogs
                    ? 'bg-accent/10 border-accent/40 text-accent cursor-wait select-none'
                    : 'bg-white border-[var(--color-border)] hover:border-accent/50 text-[var(--color-ink)] hover:text-accent shadow-2xs hover:shadow-xs cursor-pointer active:scale-95'
                } disabled:opacity-50`}
              >
                {openingLogs ? (
                  <svg className="w-3.5 h-3.5 animate-spin text-accent" viewBox="0 0 24 24" fill="none">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" />
                    <path className="opacity-90" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                  </svg>
                ) : (
                  <svg className="w-3.5 h-3.5 text-[var(--color-ink-muted)] group-hover:text-accent" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" />
                  </svg>
                )}
                <span>{openingLogs ? t('opening') : t('openLogDir')}</span>
              </button>
            </div>

            {/* Audio & Temp Cache */}
            <div className="rounded-lg border border-[var(--color-border)] p-3 bg-white space-y-3">
              <div className="flex items-center justify-between">
                <h4 className="text-xs font-bold text-[var(--color-ink)]">{t('cacheTitle')}</h4>
                {cacheMb !== null && (
                  <div className="px-2.5 py-1 rounded-lg bg-amber-50 border border-amber-200/60 text-xs font-mono font-semibold text-amber-700 flex-shrink-0">
                    {t('cacheUsage')}: {cacheMb} MB
                  </div>
                )}
              </div>
              <button
                onClick={onCleanCache}
                disabled={cacheCleaning}
                className={`w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-xl border text-xs font-semibold shadow-2xs transition-all ${
                  cacheCleaning
                    ? 'bg-red-50 text-red-600 border-red-300 shadow-[0_0_10px_rgba(239,68,68,0.2)] cursor-wait select-none'
                    : 'bg-white border-red-200 hover:border-red-400 hover:bg-red-50/50 text-red-600 hover:shadow-xs cursor-pointer active:scale-[0.99]'
                } disabled:opacity-50`}
              >
                {cacheCleaning ? (
                  <svg className="w-3.5 h-3.5 animate-spin text-red-500" viewBox="0 0 24 24" fill="none">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" />
                    <path className="opacity-90" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                  </svg>
                ) : (
                  <svg className="w-3.5 h-3.5 text-red-500" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M3 6h18M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
                  </svg>
                )}
                <span>{cacheCleaning ? t('cleaning') : t('cleanCache')}</span>
              </button>
            </div>
          </Disclosure>
        </div>

        {/* Footer */}
        <div className="px-6 py-3 border-t border-[var(--color-border)] bg-gray-50/50 flex justify-end flex-shrink-0">
          <button
            onClick={onClose}
            className="px-5 py-1.5 rounded-xl bg-[var(--color-ink)] hover:bg-black text-white text-xs font-medium shadow-xs transition-colors cursor-pointer"
          >
            {t('close')}
          </button>
        </div>
      </div>
    </div>
  );
}
