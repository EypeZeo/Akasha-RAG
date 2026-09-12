import { useState, useEffect, useCallback, useRef } from 'react';
import * as api from '../api';
import { useI18n } from '../i18n';

interface Props {
  onClose: () => void;
  onSuccess: () => void;
  initialPlatform?: 'douyin' | 'bilibili';
}

export default function LoginModal({ onClose, onSuccess, initialPlatform = 'douyin' }: Props) {
  const { t } = useI18n();
  const [platform, setPlatform] = useState<'douyin' | 'bilibili'>(initialPlatform);

  // Douyin state
  const [dyQrImg, setDyQrImg] = useState<string | null>(null);
  const [dyStatus, setDyStatus] = useState<'loading' | 'pending' | 'syncing' | 'success' | 'expired' | 'failed'>('loading');
  const [dyMessage, setDyMessage] = useState('');
  const [dyRenderError, setDyRenderError] = useState(false);
  const [dyWindowOpened, setDyWindowOpened] = useState(false);

  // Bilibili state
  const [biliQrKey, setBiliQrKey] = useState<string | null>(null);
  const [biliQrImg, setBiliQrImg] = useState<string | null>(null);
  const [biliStatus, setBiliStatus] = useState<'loading' | 'pending' | 'scanned' | 'success' | 'expired' | 'failed'>('loading');
  const [biliMessage, setBiliMessage] = useState('');
  const [biliUser, setBiliUser] = useState<{ nickname?: string; avatar_url?: string } | null>(null);
  const [qrRenderError, setQrRenderError] = useState(false);

  const biliPollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const dyPollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // ---- Bilibili Flow ----
  const loadBilibiliQr = useCallback(async () => {
    if (biliPollRef.current) {
      clearInterval(biliPollRef.current);
      biliPollRef.current = null;
    }
    setBiliStatus('loading');
    setBiliMessage(t('loggingIn'));
    setBiliQrImg(null);
    setBiliQrKey(null);
    setQrRenderError(false);

    try {
      const res = await api.bilibiliGenerateQr();
      if (res.success && res.data) {
        setBiliQrKey(res.data.qrcode_key);
        setBiliQrImg(res.data.qrcode_image_base64);
        setBiliStatus('pending');
        setBiliMessage(t('scanWithBiliApp'));
      } else {
        setBiliStatus('failed');
        setBiliMessage(res.message || t('loginFailed'));
      }
    } catch (e: any) {
      setBiliStatus('failed');
      setBiliMessage(e.message || t('networkError'));
    }
  }, [t]);

  // Bilibili polling
  useEffect(() => {
    if (platform !== 'bilibili' || !biliQrKey || biliStatus === 'success' || biliStatus === 'expired' || biliStatus === 'failed') {
      return;
    }

    biliPollRef.current = setInterval(async () => {
      try {
        const res = await api.bilibiliPollQr(biliQrKey);
        if (res.success) {
          // Backend's provider-neutral QR contract uses `confirmed`; retain
          // `success` for old servers during rolling upgrades.
          if (res.status === 'success' || res.status === 'confirmed') {
            setBiliStatus('success');
            setBiliMessage(t('bilibiliLoginSuccess'));
            if (res.nickname || res.avatar_url) {
              setBiliUser({ nickname: res.nickname, avatar_url: res.avatar_url });
            }
            if (biliPollRef.current) clearInterval(biliPollRef.current);
            setTimeout(onSuccess, 900);
          } else if (res.status === 'scanned') {
            setBiliStatus('scanned');
            setBiliMessage(t('bilibiliQrScanned'));
          } else if (res.status === 'expired') {
            setBiliStatus('expired');
            setBiliMessage(t('qrExpiredClickRefresh'));
            if (biliPollRef.current) clearInterval(biliPollRef.current);
          } else if (res.status === 'pending') {
            setBiliStatus('pending');
            setBiliMessage(t('bilibiliQrWaitingScan'));
          }
        }
      } catch {
        /* network error retry */
      }
    }, 1500);

    return () => {
      if (biliPollRef.current) {
        clearInterval(biliPollRef.current);
        biliPollRef.current = null;
      }
    };
  }, [platform, biliQrKey, biliStatus, onSuccess, t]);

  // ---- Douyin Flow ----
  const loadDouyinQr = useCallback(async () => {
    if (dyPollRef.current) {
      clearInterval(dyPollRef.current);
      dyPollRef.current = null;
    }
    setDyStatus('loading');
    setDyMessage(t('loggingIn'));
    setDyQrImg(null);
    setDyRenderError(false);
    setDyWindowOpened(false);

    try {
      const res = await api.douyinGenerateQr();
      if (res.success && res.data?.qrcode_image_base64) {
        setDyQrImg(res.data.qrcode_image_base64);
        setDyStatus('pending');
        setDyMessage(t('scanWithDouyinApp'));
      } else {
        setDyStatus('pending');
        setDyMessage(res.message || t('scanWithDouyinApp'));
      }
    } catch (e: any) {
      setDyStatus('failed');
      setDyMessage(e.message || t('networkError'));
    }
  }, [t]);

  const refreshDouyinQr = async () => {
    setDyStatus('loading');
    setDyMessage(t('loggingIn'));
    try {
      const res = await api.douyinRefreshQr();
      if (res.success && res.data?.qrcode_image_base64) {
        setDyQrImg(res.data.qrcode_image_base64);
        setDyStatus('pending');
        setDyMessage(t('scanWithDouyinApp'));
      } else {
        await loadDouyinQr();
      }
    } catch {
      await loadDouyinQr();
    }
  };

  const handleShowDouyinWindow = async () => {
    try {
      await api.douyinShowWindow();
      setDyWindowOpened(true);
      setDyMessage(t('windowOpened'));
    } catch (e: any) {
      console.warn('Show window failed:', e);
    }
  };

  // Douyin polling
  useEffect(() => {
    if (platform !== 'douyin' || dyStatus === 'success' || dyStatus === 'failed') return;

    dyPollRef.current = setInterval(async () => {
      try {
        const s = await api.loginStatus();
        if (s.qrcode_image_base64) {
          setDyQrImg(s.qrcode_image_base64);
        }
        if (s.status === 'syncing') {
          setDyStatus('syncing');
          setDyMessage(s.message || t('loginSyncing'));
        } else if (s.status === 'logged_in') {
          setDyStatus('success');
          setDyMessage(t('loginSuccessDone'));
          if (dyPollRef.current) clearInterval(dyPollRef.current);
          setTimeout(onSuccess, 900);
        } else if (s.status === 'failed') {
          setDyStatus('failed');
          setDyMessage(s.message || t('loginFailed'));
          if (dyPollRef.current) clearInterval(dyPollRef.current);
        }
      } catch {
        /* ignore network error */
      }
    }, 1500);

    return () => {
      if (dyPollRef.current) {
        clearInterval(dyPollRef.current);
        dyPollRef.current = null;
      }
    };
  }, [platform, dyStatus, onSuccess, t]);

  // Initial load when platform changes
  useEffect(() => {
    if (platform === 'bilibili') {
      loadBilibiliQr();
    } else if (platform === 'douyin') {
      loadDouyinQr();
    }
  }, [platform, loadBilibiliQr, loadDouyinQr]);

  const handleClose = async () => {
    if (biliPollRef.current) {
      clearInterval(biliPollRef.current);
      biliPollRef.current = null;
    }
    if (dyPollRef.current) {
      clearInterval(dyPollRef.current);
      dyPollRef.current = null;
    }
    // 仅在尚未扫码成功的等待状态下主动关闭，才取消未完成的登录；若已扫码进入同步则绝不取消
    if (platform === 'douyin' && (dyStatus === 'pending' || dyStatus === 'loading')) {
      try {
        await api.loginCancel();
      } catch {}
    }
    // 若扫码已确认并进入同步或已完成，通知父页面立即更新平台状态
    if (dyStatus === 'syncing' || dyStatus === 'success' || biliStatus === 'success') {
      onSuccess();
    }
    onClose();
  };

  const switchPlatform = async (target: 'douyin' | 'bilibili') => {
    if (target === platform) return;
    if (platform === 'douyin' && (dyStatus === 'pending' || dyStatus === 'loading')) {
      try { await api.loginCancel(); } catch {}
    }
    if (biliPollRef.current) {
      clearInterval(biliPollRef.current);
      biliPollRef.current = null;
    }
    if (dyPollRef.current) {
      clearInterval(dyPollRef.current);
      dyPollRef.current = null;
    }
    setPlatform(target);
  };

  return (
    <div className="fixed inset-0 bg-black/45 flex items-center justify-center z-50 backdrop-blur-xs p-4 animate-fade-in" onClick={handleClose}>
      <div
        className="bg-[var(--color-panel)] rounded-2xl p-7 w-full max-w-[420px] flex flex-col items-center gap-4 shadow-2xl border border-[var(--color-border)] animate-scale-up"
        onClick={e => e.stopPropagation()}
      >
        {/* Platform Switcher Tabs */}
        <div className="w-full flex items-center bg-black/5 p-1 rounded-xl">
          <button
            type="button"
            onClick={() => switchPlatform('douyin')}
            className={`flex-1 flex items-center justify-center gap-1.5 py-1.5 rounded-lg text-xs font-semibold transition-all cursor-pointer ${
              platform === 'douyin'
                ? 'bg-white text-accent shadow-xs'
                : 'text-[var(--color-ink-muted)] hover:text-[var(--color-ink)]'
            }`}
          >
            <img
              src="/platform-icons/douyin.svg"
              alt=""
              aria-hidden="true"
              className="w-4 h-4 object-contain"
            />
            <span>{t('platformDouyin')}</span>
          </button>
          <button
            type="button"
            onClick={() => switchPlatform('bilibili')}
            className={`flex-1 flex items-center justify-center gap-1.5 py-1.5 rounded-lg text-xs font-semibold transition-all cursor-pointer ${
              platform === 'bilibili'
                ? 'bg-white text-pink-600 shadow-xs'
                : 'text-[var(--color-ink-muted)] hover:text-[var(--color-ink)]'
            }`}
          >
            <img
              src="/platform-icons/bilibili.svg"
              alt=""
              aria-hidden="true"
              className="w-4 h-4 object-contain"
            />
            <span>{t('platformBilibili')}</span>
          </button>
        </div>

        {/* Modal Title */}
        <h2 className="font-display text-lg font-bold text-[var(--color-ink)] text-center">
          {platform === 'douyin' ? (
            dyStatus === 'success'
              ? t('loginSuccessDone')
              : dyStatus === 'syncing'
              ? t('loginSyncing')
              : dyStatus === 'failed'
              ? t('loginFailed')
              : t('loginDouyin')
          ) : (
            biliStatus === 'success'
              ? (biliUser?.nickname ? `🎉 欢迎，${biliUser.nickname}` : t('bilibiliLoginSuccess'))
              : biliStatus === 'scanned'
              ? t('bilibiliQrScanned')
              : biliStatus === 'expired'
              ? t('qrExpiredClickRefresh')
              : t('loginBilibili')
          )}
        </h2>

        {/* QR Code / Illustration Box */}
        <div className="w-52 h-52 rounded-2xl bg-black/[0.03] flex items-center justify-center relative border border-black/5 overflow-hidden p-2">
          {platform === 'douyin' ? (
            <>
              {dyStatus === 'loading' && (
                <div className="flex flex-col items-center gap-2 text-xs text-[var(--color-ink-muted)]">
                  <span className="w-8 h-8 rounded-full border-3 border-accent/20 border-t-accent animate-spin" />
                  <span>{t('loggingIn')}</span>
                </div>
              )}
              {dyStatus === 'failed' && (
                <div className="flex flex-col items-center gap-2 text-xs text-red-500 text-center p-3">
                  <span className="text-3xl">❌</span>
                  <span>{dyMessage || t('loginFailed')}</span>
                  <button
                    onClick={loadDouyinQr}
                    className="mt-1 px-3 py-1 bg-red-50 text-red-600 rounded-lg text-xs font-semibold cursor-pointer hover:bg-red-100 transition-colors"
                  >
                    {t('retry')}
                  </button>
                </div>
              )}
              {dyStatus === 'syncing' && (
                <div className="flex flex-col items-center justify-center gap-3.5 text-center p-3 animate-fade-in w-full h-full">
                  <div className="relative w-16 h-16 flex items-center justify-center">
                    {/* Subtle pulse halo */}
                    <div className="absolute inset-0 rounded-full bg-accent/20 animate-ping opacity-60 pointer-events-none" />
                    {/* Smooth SVG rotating ring */}
                    <svg className="w-16 h-16 animate-spin text-accent" viewBox="0 0 50 50">
                      <circle
                        className="text-accent/20"
                        cx="25"
                        cy="25"
                        r="20"
                        stroke="currentColor"
                        strokeWidth="3"
                        fill="none"
                      />
                      <circle
                        className="text-accent"
                        cx="25"
                        cy="25"
                        r="20"
                        stroke="currentColor"
                        strokeWidth="3"
                        strokeDasharray="80"
                        strokeDashoffset="60"
                        strokeLinecap="round"
                        fill="none"
                      />
                    </svg>
                    {/* Centered stationary Douyin logo - does NOT spin */}
                    <div className="absolute inset-0 flex items-center justify-center">
                      <div className="w-8 h-8 rounded-lg bg-white shadow-xs border border-black/5 flex items-center justify-center p-1">
                        <img src="/platform-icons/douyin.svg" alt="Douyin" className="w-full h-full object-contain" />
                      </div>
                    </div>
                  </div>
                  <div className="flex flex-col items-center gap-1">
                    <span className="text-xs font-bold text-accent">{t('loginSyncing')}</span>
                    <span className="text-[11px] text-[var(--color-ink-muted)]">正在同步收藏夹与视频数据…</span>
                  </div>
                </div>
              )}
              {dyStatus === 'success' && (
                <div className="flex flex-col items-center gap-2 animate-scale-up">
                  <span className="text-5xl">✅</span>
                  <span className="text-xs font-bold text-emerald-600">{t('loginSuccessDone')}</span>
                </div>
              )}
              {(dyStatus === 'pending' || dyStatus === 'expired') && dyQrImg && (
                <div className="relative w-full h-full flex items-center justify-center">
                  {dyRenderError ? (
                    <div className="flex flex-col items-center gap-2 text-xs text-red-500 text-center p-3">
                      <span className="text-3xl">⚠️</span>
                      <span>{t('imageLoadFailed')}</span>
                      <button
                        onClick={loadDouyinQr}
                        className="mt-1 px-3 py-1 bg-red-50 text-red-600 rounded-lg text-xs font-semibold cursor-pointer hover:bg-red-100 transition-colors"
                      >
                        {t('retry')}
                      </button>
                    </div>
                  ) : (
                    <>
                      <img
                        src={dyQrImg.trim().startsWith('data:') ? dyQrImg.trim() : `data:image/png;base64,${dyQrImg.trim()}`}
                        alt="Douyin QR Code"
                        className={`w-44 h-44 object-contain rounded-xl ${dyStatus === 'expired' ? 'blur-xs opacity-30' : ''}`}
                        onError={() => setDyRenderError(true)}
                      />
                      {dyStatus === 'expired' && (
                        <button
                          onClick={refreshDouyinQr}
                          className="absolute inset-0 flex flex-col items-center justify-center bg-black/40 rounded-xl text-white gap-1 cursor-pointer hover:bg-black/50 transition-colors p-2 text-center"
                        >
                          <span className="text-2xl">🔄</span>
                          <span className="text-xs font-semibold">{t('qrExpiredClickRefresh')}</span>
                        </button>
                      )}
                    </>
                  )}
                </div>
              )}
              {dyStatus === 'pending' && !dyQrImg && (
                <div className="flex flex-col items-center gap-3 text-center">
                  <span className="w-8 h-8 rounded-full border-3 border-accent/20 border-t-accent animate-spin" />
                  <span className="text-xs font-medium text-[var(--color-ink-soft)]">正在生成登录二维码…</span>
                </div>
              )}
            </>
          ) : (
            <>
              {biliStatus === 'loading' && (
                <div className="flex flex-col items-center gap-2 text-xs text-[var(--color-ink-muted)]">
                  <span className="text-3xl animate-spin">⏳</span>
                  <span>{t('loggingIn')}</span>
                </div>
              )}
              {biliStatus === 'failed' && (
                <div className="flex flex-col items-center gap-2 text-xs text-red-500 text-center p-3">
                  <span className="text-3xl">❌</span>
                  <span>{biliMessage || t('loginFailed')}</span>
                  <button
                    onClick={loadBilibiliQr}
                    className="mt-1 px-3 py-1 bg-red-50 text-red-600 rounded-lg text-xs font-semibold cursor-pointer"
                  >
                    {t('retry')}
                  </button>
                </div>
              )}
              {biliStatus === 'success' && (
                <div className="flex flex-col items-center gap-2">
                  {biliUser?.avatar_url && !qrRenderError ? (
                    <img
                      src={biliUser.avatar_url}
                      alt=""
                      referrerPolicy="no-referrer"
                      onError={() => setQrRenderError(true)}
                      className="w-16 h-16 rounded-full border-2 border-pink-400 shadow-md"
                    />
                  ) : (
                    <span className="text-5xl">✅</span>
                  )}
                  <span className="text-xs font-bold text-pink-600">{biliUser?.nickname || t('bilibiliLoginSuccess')}</span>
                </div>
              )}
              {(biliStatus === 'pending' || biliStatus === 'scanned' || biliStatus === 'expired') && biliQrImg && (
                <div className="relative w-full h-full flex items-center justify-center">
                  {qrRenderError ? (
                    <div className="flex flex-col items-center gap-2 text-xs text-red-500 text-center p-3">
                      <span className="text-3xl">⚠️</span>
                      <span>{t('imageLoadFailed')}</span>
                      <button
                        onClick={loadBilibiliQr}
                        className="mt-1 px-3 py-1 bg-red-50 text-red-600 rounded-lg text-xs font-semibold cursor-pointer hover:bg-red-100 transition-colors"
                      >
                        {t('retry')}
                      </button>
                    </div>
                  ) : (
                    <>
                      <img
                        src={biliQrImg.trim().startsWith('data:') ? biliQrImg.trim() : `data:image/png;base64,${biliQrImg.trim()}`}
                        alt="Bilibili QR Code"
                        className={`w-44 h-44 object-contain rounded-xl ${biliStatus === 'expired' ? 'blur-xs opacity-30' : ''}`}
                        onError={() => setQrRenderError(true)}
                      />
                      {biliStatus === 'scanned' && (
                        <div className="absolute inset-0 bg-pink-500/80 rounded-xl flex flex-col items-center justify-center text-white gap-1 p-2 text-center animate-fade-in">
                          <span className="text-3xl">📱</span>
                          <span className="text-xs font-bold">{t('bilibiliQrScanned')}</span>
                        </div>
                      )}
                      {biliStatus === 'expired' && (
                        <button
                          onClick={loadBilibiliQr}
                          className="absolute inset-0 flex flex-col items-center justify-center bg-black/40 rounded-xl text-white gap-1 cursor-pointer hover:bg-black/50 transition-colors p-2 text-center"
                        >
                          <span className="text-2xl">🔄</span>
                          <span className="text-xs font-semibold">{t('qrExpiredClickRefresh')}</span>
                        </button>
                      )}
                    </>
                  )}
                </div>
              )}
            </>
          )}
        </div>

        {/* Message / Status Description */}
        <p className="text-xs text-[var(--color-ink-soft)] text-center font-medium max-w-xs">
          {platform === 'douyin' ? (dyMessage || t('scanWithDouyinApp')) : biliMessage}
        </p>

        {/* Instructions */}
        {platform === 'douyin' ? (
          dyStatus === 'syncing' ? (
            <div className="w-full flex items-center justify-center gap-2 text-xs text-accent bg-accent-light/50 p-3 rounded-xl border border-accent/20">
              <span className="w-2 h-2 rounded-full bg-accent animate-ping" />
              <span>扫码已确认，正在后台同步数据，可随时关闭窗口</span>
            </div>
          ) : (
            <div className="w-full flex flex-col gap-1.5 text-xs text-[var(--color-ink-soft)] bg-black/[0.02] p-3 rounded-xl border border-black/5">
              <div className="flex gap-2"><span className="text-accent font-bold">1.</span>{t('scanWithDouyinApp')}</div>
              <div className="flex gap-2"><span className="text-accent font-bold">2.</span>{t('douyinStep2')}</div>
              <div className="flex gap-2"><span className="text-accent font-bold">3.</span>{t('douyinStep3')}</div>
            </div>
          )
        ) : (
          <div className="w-full flex flex-col gap-1.5 text-xs text-[var(--color-ink-soft)] bg-black/[0.02] p-3 rounded-xl border border-black/5">
            <div className="flex gap-2"><span className="text-pink-600 font-bold">1.</span>{t('scanWithBiliApp')}</div>
            <div className="flex gap-2"><span className="text-pink-600 font-bold">2.</span>{t('biliStep2')}</div>
            <div className="flex gap-2"><span className="text-pink-600 font-bold">3.</span>{t('biliStep3')}</div>
          </div>
        )}

        {/* Escape hatch for Douyin window display */}
        {platform === 'douyin' && (dyStatus === 'pending' || dyStatus === 'loading') && (
          <button
            type="button"
            onClick={handleShowDouyinWindow}
            className="text-[11px] text-[var(--color-ink-muted)] hover:text-accent transition-colors underline cursor-pointer -mt-1"
          >
            {dyWindowOpened ? t('windowOpened') : t('needCaptchaOpenWindow')}
          </button>
        )}

        <button
          onClick={handleClose}
          className="text-xs text-[var(--color-ink-muted)] hover:text-red-500 transition-colors px-4 py-1.5 rounded-lg hover:bg-black/5 cursor-pointer mt-1"
        >
          {(platform === 'douyin' ? (dyStatus === 'success' || dyStatus === 'syncing') : biliStatus === 'success') ? t('close') : t('cancelLogin')}
        </button>
      </div>
    </div>
  );
}
