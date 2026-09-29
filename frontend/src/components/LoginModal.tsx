import { useCallback, useEffect, useId, useRef, useState } from 'react';
import * as api from '../api';
import { useI18n } from '../i18n';
import Dialog from './ui/Dialog';

interface Props {
  onClose: () => void;
  onSuccess: () => void;
  initialPlatform?: api.PlatformKind;
}

type DouyinStatus = 'loading' | 'pending' | 'syncing' | 'success' | 'expired' | 'failed';
type BilibiliStatus = 'loading' | 'pending' | 'scanned' | 'success' | 'expired' | 'failed';
type ZhihuStatus = 'idle' | 'pending' | 'logged_in' | 'expired' | 'failed';

const PLATFORM_ORDER: api.PlatformKind[] = ['douyin', 'bilibili', 'zhihu'];

function Spinner({ color = 'accent' }: { color?: 'accent' | 'pink' | 'blue' }) {
  const border = color === 'pink' ? 'border-pink-200 border-t-pink-600' : color === 'blue' ? 'border-blue-200 border-t-blue-600' : 'border-accent/20 border-t-accent';
  return <span className={`w-8 h-8 rounded-full border-3 animate-spin ${border}`} />;
}

export default function LoginModal({ onClose, onSuccess, initialPlatform = 'douyin' }: Props) {
  const { t } = useI18n();
  const titleId = useId();
  const [platform, setPlatform] = useState<api.PlatformKind>(initialPlatform);
  const [platformsReady, setPlatformsReady] = useState(false);
  const [loggedInPlatforms, setLoggedInPlatforms] = useState<Set<api.PlatformKind>>(() => new Set());
  const [dyQrImg, setDyQrImg] = useState<string | null>(null);
  const [dyStatus, setDyStatus] = useState<DouyinStatus>('loading');
  const [dyMessage, setDyMessage] = useState('');
  const [dyWindowOpened, setDyWindowOpened] = useState(false);
  const [biliQrKey, setBiliQrKey] = useState<string | null>(null);
  const [biliQrImg, setBiliQrImg] = useState<string | null>(null);
  const [biliStatus, setBiliStatus] = useState<BilibiliStatus>('loading');
  const [biliMessage, setBiliMessage] = useState('');
  const [zhihuStatus, setZhihuStatus] = useState<ZhihuStatus>('idle');
  const [zhihuPollingReady, setZhihuPollingReady] = useState(false);
  const [zhihuMessage, setZhihuMessage] = useState('');
  const [zhihuQrImg, setZhihuQrImg] = useState<string | null>(null);

  const dyPollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const biliPollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const zhihuPollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const completionTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const mountedRef = useRef(false);
  const platformRef = useRef<api.PlatformKind>(initialPlatform);
  const generationRef = useRef(0);
  const tRef = useRef(t);
  const onSuccessRef = useRef(onSuccess);
  tRef.current = t;
  onSuccessRef.current = onSuccess;

  const clearPoll = (ref: React.MutableRefObject<ReturnType<typeof setInterval> | null>) => {
    if (ref.current) clearInterval(ref.current);
    ref.current = null;
  };

  const clearCompletionTimer = () => {
    if (completionTimerRef.current) clearTimeout(completionTimerRef.current);
    completionTimerRef.current = null;
  };

  const invalidateLogin = useCallback(() => {
    generationRef.current += 1;
    clearPoll(dyPollRef);
    clearPoll(biliPollRef);
    clearPoll(zhihuPollRef);
    clearCompletionTimer();
  }, []);

  const isCurrentLogin = useCallback((generation: number, expectedPlatform: api.PlatformKind) => (
    mountedRef.current
    && generationRef.current === generation
    && platformRef.current === expectedPlatform
  ), []);

  const scheduleSuccess = useCallback((generation: number, expectedPlatform: api.PlatformKind) => {
    clearCompletionTimer();
    completionTimerRef.current = setTimeout(() => {
      if (isCurrentLogin(generation, expectedPlatform)) onSuccessRef.current();
    }, 900);
  }, [isCurrentLogin]);

  const loadDouyin = useCallback(async (generation: number) => {
    if (!isCurrentLogin(generation, 'douyin')) return;
    setDyStatus('loading');
    setDyMessage(tRef.current('loggingIn'));
    try {
      const result = await api.douyinGenerateQr();
      if (!result.success || !result.data?.qrcode_image_base64) {
        throw new Error('QR generation failed');
      }
      if (!isCurrentLogin(generation, 'douyin')) return;
      setDyQrImg(result.data.qrcode_image_base64);
      setDyStatus('pending');
      setDyMessage(tRef.current('scanWithDouyinApp'));
    } catch (error) {
      if (!isCurrentLogin(generation, 'douyin')) return;
      console.warn('Douyin QR generation failed:', error);
      setDyStatus('failed');
      setDyMessage(tRef.current('networkError'));
    }
  }, [isCurrentLogin]);

  const loadBilibili = useCallback(async (generation: number) => {
    if (!isCurrentLogin(generation, 'bilibili')) return;
    setBiliStatus('loading');
    setBiliMessage(tRef.current('loggingIn'));
    try {
      const result = await api.bilibiliGenerateQr();
      if (!result.success || !result.data) throw new Error('QR generation failed');
      if (!isCurrentLogin(generation, 'bilibili')) return;
      setBiliQrKey(result.data.qrcode_key);
      setBiliQrImg(result.data.qrcode_image_base64);
      setBiliStatus('pending');
      setBiliMessage(tRef.current('scanWithBiliApp'));
    } catch (error) {
      if (!isCurrentLogin(generation, 'bilibili')) return;
      console.warn('Bilibili QR generation failed:', error);
      setBiliStatus('failed');
      setBiliMessage(tRef.current('networkError'));
    }
  }, [isCurrentLogin]);

  const loadZhihu = useCallback(async (generation: number) => {
    if (!isCurrentLogin(generation, 'zhihu')) return;
    setZhihuStatus('pending');
    setZhihuPollingReady(false);
    setZhihuQrImg(null);
    setZhihuMessage(tRef.current('zhihuLoginOpening'));
    try {
      const result = await api.zhihuLoginStart();
      if (!isCurrentLogin(generation, 'zhihu')) return;
      if (result.status === 'logged_in') {
        setZhihuStatus('logged_in');
        setZhihuMessage(result.message || tRef.current('zhihuLoginSuccess'));
        scheduleSuccess(generation, 'zhihu');
        return;
      }
      const qrImage = result.qrcode_image_base64 || null;
      setZhihuQrImg(qrImage);
      // The worker starts before Zhihu has rendered a complete QR canvas.
      // Never instruct users to scan until a real image is available.
      setZhihuMessage(qrImage ? (result.message || tRef.current('zhihuLoginWaiting')) : tRef.current('zhihuLoginOpening'));
      setZhihuPollingReady(true);
    } catch (error) {
      if (!isCurrentLogin(generation, 'zhihu')) return;
      console.warn('Zhihu login start failed:', error);
      setZhihuStatus('failed');
      setZhihuPollingReady(false);
      setZhihuMessage(tRef.current('networkError'));
    }
  }, [isCurrentLogin, scheduleSuccess]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      invalidateLogin();
    };
  }, [invalidateLogin]);

  useEffect(() => {
    let cancelled = false;
    void api.listPlatforms()
      .then((result) => {
        if (cancelled || !mountedRef.current) return;
        const loggedIn = new Set<api.PlatformKind>(
          (result.platforms || []).filter((item) => item.is_logged_in).map((item) => item.platform),
        );
        setLoggedInPlatforms(loggedIn);
        setPlatform((current) => loggedIn.has(current)
          ? PLATFORM_ORDER.find((candidate) => !loggedIn.has(candidate)) || current
          : current);
      })
      .catch((error) => {
        // A transient status-read failure must not falsely disable a login option.
        console.warn('Platform login status load failed:', error);
      })
      .finally(() => {
        if (!cancelled && mountedRef.current) setPlatformsReady(true);
      });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    platformRef.current = platform;
    if (!platformsReady || loggedInPlatforms.has(platform)) return;
    const generation = generationRef.current + 1;
    generationRef.current = generation;
    if (platform === 'douyin') void loadDouyin(generation);
    else if (platform === 'bilibili') void loadBilibili(generation);
    else void loadZhihu(generation);
    return () => {
      if (generationRef.current === generation) invalidateLogin();
    };
  }, [platform, platformsReady, loggedInPlatforms, invalidateLogin, loadBilibili, loadDouyin, loadZhihu]);

  useEffect(() => {
    if (!platformsReady || platform !== 'douyin' || (dyStatus !== 'pending' && dyStatus !== 'syncing')) return;
    const generation = generationRef.current;
    let inFlight = false;
    dyPollRef.current = setInterval(async () => {
      if (inFlight || !isCurrentLogin(generation, 'douyin')) return;
      inFlight = true;
      try {
        const result = await api.loginStatus();
        if (!isCurrentLogin(generation, 'douyin')) return;
        if (result.qrcode_image_base64) setDyQrImg(result.qrcode_image_base64);
        if (result.status === 'syncing') {
          setDyStatus('syncing');
          setDyMessage(tRef.current('loginSyncing'));
        } else if (result.status === 'logged_in') {
          clearPoll(dyPollRef);
          setDyStatus('success');
          setDyMessage(tRef.current('loginSuccessDone'));
          scheduleSuccess(generation, 'douyin');
        } else if (result.status === 'failed') {
          clearPoll(dyPollRef);
          setDyStatus('failed');
          setDyMessage(tRef.current('loginFailed'));
        } else if (result.status === 'expired') {
          clearPoll(dyPollRef);
          setDyStatus('expired');
          setDyMessage(tRef.current('qrExpiredClickRefresh'));
        }
      } catch (error) {
        console.warn('Douyin login status poll failed:', error);
      } finally {
        inFlight = false;
      }
    }, 1500);
    return () => clearPoll(dyPollRef);
  }, [dyStatus, isCurrentLogin, platform, platformsReady, scheduleSuccess]);

  useEffect(() => {
    if (!platformsReady || platform !== 'bilibili' || !biliQrKey || biliStatus === 'success' || biliStatus === 'expired' || biliStatus === 'failed') return;
    const generation = generationRef.current;
    let inFlight = false;
    biliPollRef.current = setInterval(async () => {
      if (inFlight || !isCurrentLogin(generation, 'bilibili')) return;
      inFlight = true;
      try {
        const result = await api.bilibiliPollQr(biliQrKey);
        if (!isCurrentLogin(generation, 'bilibili')) return;
        if (result.status === 'confirmed' || result.status === 'success') {
          clearPoll(biliPollRef);
          setBiliStatus('success');
          setBiliMessage(tRef.current('bilibiliLoginSuccess'));
          scheduleSuccess(generation, 'bilibili');
        } else if (result.status === 'scanned') {
          setBiliStatus('scanned');
          setBiliMessage(tRef.current('bilibiliQrScanned'));
        } else if (result.status === 'expired') {
          clearPoll(biliPollRef);
          setBiliStatus('expired');
          setBiliMessage(tRef.current('qrExpiredClickRefresh'));
        }
      } catch (error) {
        console.warn('Bilibili login status poll failed:', error);
      } finally {
        inFlight = false;
      }
    }, 1500);
    return () => clearPoll(biliPollRef);
  }, [biliQrKey, biliStatus, isCurrentLogin, platform, platformsReady, scheduleSuccess]);

  useEffect(() => {
    if (!platformsReady || platform !== 'zhihu' || !zhihuPollingReady || zhihuStatus !== 'pending') return;
    const generation = generationRef.current;
    let inFlight = false;
    zhihuPollRef.current = setInterval(async () => {
      if (inFlight || !isCurrentLogin(generation, 'zhihu')) return;
      inFlight = true;
      try {
        const result = await api.zhihuLoginStatus();
        if (!isCurrentLogin(generation, 'zhihu')) return;
        const qrImage = result.qrcode_image_base64 || null;
        if (qrImage) setZhihuQrImg(qrImage);
        setZhihuMessage(qrImage ? (result.message || tRef.current('zhihuLoginWaiting')) : tRef.current('zhihuLoginOpening'));
        if (result.status === 'logged_in') {
          clearPoll(zhihuPollRef);
          setZhihuStatus('logged_in');
          setZhihuPollingReady(false);
          scheduleSuccess(generation, 'zhihu');
        } else if (result.status === 'expired' || result.status === 'failed') {
          clearPoll(zhihuPollRef);
          setZhihuStatus(result.status);
          setZhihuPollingReady(false);
        }
      } catch (error) {
        console.warn('Zhihu login status poll failed:', error);
      } finally {
        inFlight = false;
      }
    }, 1500);
    return () => clearPoll(zhihuPollRef);
  }, [isCurrentLogin, platform, platformsReady, scheduleSuccess, zhihuPollingReady, zhihuStatus]);

  const switchPlatform = async (next: api.PlatformKind) => {
    if (!platformsReady || next === platform || loggedInPlatforms.has(next)) return;
    const previousPlatform = platformRef.current;
    const previousDouyinStatus = dyStatus;
    const previousZhihuStatus = zhihuStatus;
    invalidateLogin();
    if (previousPlatform === 'douyin' && (previousDouyinStatus === 'pending' || previousDouyinStatus === 'loading')) {
      try { await api.loginCancel(); } catch (error) { console.warn('Douyin login cancel failed:', error); }
    }
    if (previousPlatform === 'zhihu' && previousZhihuStatus === 'pending') {
      try { await api.zhihuLoginCancel(); } catch (error) { console.warn('Zhihu login cancel failed:', error); }
    }
    platformRef.current = next;
    setPlatform(next);
  };

  const handleClose = async () => {
    const currentPlatform = platformRef.current;
    const completed = dyStatus === 'syncing' || dyStatus === 'success' || biliStatus === 'success' || zhihuStatus === 'logged_in';
    invalidateLogin();
    if (currentPlatform === 'douyin' && (dyStatus === 'pending' || dyStatus === 'loading')) {
      try { await api.loginCancel(); } catch (error) { console.warn('Douyin login cancel failed:', error); }
    }
    if (currentPlatform === 'zhihu' && zhihuStatus === 'pending') {
      try { await api.zhihuLoginCancel(); } catch (error) { console.warn('Zhihu login cancel failed:', error); }
    }
    if (completed) onSuccessRef.current();
    onClose();
  };

  const platformTabs: Array<{ id: api.PlatformKind; label: string; icon: string }> = [
    { id: 'douyin', label: t('platformDouyin'), icon: '/platform-icons/douyin.svg' },
    { id: 'bilibili', label: t('platformBilibili'), icon: '/platform-icons/bilibili.svg' },
    { id: 'zhihu', label: t('platformZhihu'), icon: '/platform-icons/zhihu.svg' },
  ];
  const allPlatformsLoggedIn = platformsReady && PLATFORM_ORDER.every((item) => loggedInPlatforms.has(item));
  const title = allPlatformsLoggedIn
    ? t('allPlatformsLoggedIn')
    : platform === 'douyin'
      ? dyStatus === 'success' ? t('loginSuccessDone') : dyStatus === 'syncing' ? t('loginSyncing') : t('loginDouyin')
      : platform === 'bilibili'
        ? biliStatus === 'success' ? t('bilibiliLoginSuccess') : t('loginBilibili')
        : zhihuStatus === 'logged_in' ? t('zhihuLoginSuccess') : t('loginZhihu');
  const instructions = platform === 'douyin'
    ? [t('scanWithDouyinApp'), t('douyinStep2'), t('douyinStep3')]
    : platform === 'bilibili'
      ? [t('scanWithBiliApp'), t('biliStep2'), t('biliStep3')]
      : zhihuStatus === 'pending' && !zhihuQrImg
        ? [t('zhihuLoginOpening')]
        : [t('zhihuStep1'), t('zhihuStep2'), t('zhihuStep3')];

  return (
    <Dialog onClose={handleClose} labelledBy={titleId} className="bg-[var(--color-panel)] rounded-2xl p-7 w-full max-w-[420px] flex flex-col items-center gap-4 shadow-2xl border border-[var(--color-border)] animate-scale-up">
      <div className="w-full flex items-center bg-black/5 p-1 rounded-xl">
        {platformTabs.map((tab) => {
          const disabled = !platformsReady || loggedInPlatforms.has(tab.id);
          return (
            <button
              key={tab.id}
              type="button"
              disabled={disabled}
              aria-disabled={disabled}
              title={loggedInPlatforms.has(tab.id) ? t('loggedIn') : undefined}
              onClick={() => void switchPlatform(tab.id)}
              className={`flex-1 flex items-center justify-center gap-1.5 py-1.5 rounded-lg text-xs font-semibold transition-all ${disabled ? 'text-[var(--color-ink-muted)] opacity-50 cursor-not-allowed' : platform === tab.id ? 'bg-white text-accent shadow-xs cursor-pointer' : 'text-[var(--color-ink-muted)] hover:text-[var(--color-ink)] cursor-pointer'}`}
            >
              <img src={tab.icon} alt="" aria-hidden="true" className="w-4 h-4 object-contain shrink-0" />
              <span>{tab.label}</span>
            </button>
          );
        })}
      </div>
      <h2 id={titleId} className="font-title text-lg font-bold text-[var(--color-ink)] text-center">{title}</h2>
      {allPlatformsLoggedIn ? (
        <div className="w-52 h-52 rounded-2xl bg-black/[0.03] flex flex-col items-center justify-center gap-3 border border-black/5 text-center p-5">
          <span className="text-4xl" aria-hidden="true">✓</span>
        </div>
      ) : (
        <>
          <div className="w-52 h-52 rounded-2xl bg-black/[0.03] flex items-center justify-center relative border border-black/5 overflow-hidden p-2">
            {platform === 'douyin' && (
              <div className="flex flex-col items-center gap-3 text-center">
                {dyQrImg && <img src={dyQrImg.trim().startsWith('data:') ? dyQrImg.trim() : `data:image/png;base64,${dyQrImg.trim()}`} alt="Douyin QR Code" className={`w-44 h-44 object-contain rounded-xl ${dyStatus === 'expired' ? 'blur-xs opacity-30' : ''}`} />}
                {!dyQrImg && dyStatus !== 'failed' && dyStatus !== 'expired' && <Spinner />}
                {dyStatus === 'syncing' && <span className="text-xs font-bold text-accent">{t('loginSyncing')}</span>}
                {dyStatus === 'failed' && <><span className="text-3xl">❌</span><span className="text-xs text-red-500">{dyMessage || t('loginFailed')}</span><button type="button" onClick={() => void loadDouyin(generationRef.current)} className="text-xs text-red-600 underline cursor-pointer">{t('retry')}</button></>}
                {dyStatus === 'expired' && <button type="button" onClick={() => void loadDouyin(generationRef.current)} className="text-xs text-accent underline cursor-pointer">{t('qrExpiredClickRefresh')}</button>}
              </div>
            )}
            {platform === 'bilibili' && (
              <div className="relative flex items-center justify-center w-full h-full">
                {biliQrImg ? <img src={biliQrImg.trim().startsWith('data:') ? biliQrImg.trim() : `data:image/png;base64,${biliQrImg.trim()}`} alt="Bilibili QR Code" className="w-44 h-44 object-contain rounded-xl" /> : <Spinner color="pink" />}
                {biliStatus === 'success' && <span className="absolute text-4xl">✅</span>}
                {(biliStatus === 'failed' || biliStatus === 'expired') && <button type="button" onClick={() => void loadBilibili(generationRef.current)} className="absolute bottom-2 text-xs text-red-600 underline cursor-pointer">{t('retry')}</button>}
              </div>
            )}
            {platform === 'zhihu' && (
              <div className="flex flex-col items-center justify-center gap-3 text-center p-3 w-full h-full">
                {zhihuQrImg ? (
                  <img src={zhihuQrImg.trim().startsWith('data:') ? zhihuQrImg.trim() : `data:image/png;base64,${zhihuQrImg.trim()}`} alt="Zhihu QR Code" className="w-44 h-44 object-contain bg-white p-1" />
                ) : (
                  <>
                    <img src="/platform-icons/zhihu.svg" alt="" aria-hidden="true" className="w-14 h-14 object-contain" />
                    {zhihuStatus === 'pending' && <Spinner color="blue" />}
                  </>
                )}
                <span className="text-xs font-semibold text-blue-700">
                  {zhihuStatus === 'logged_in'
                    ? t('zhihuLoginSuccess')
                    : zhihuStatus === 'failed'
                      ? (zhihuMessage || t('loginFailed'))
                      : zhihuQrImg
                        ? t('zhihuLoginWaiting')
                        : t('zhihuLoginOpening')}
                </span>
                {(zhihuStatus === 'expired' || zhihuStatus === 'failed') && <button type="button" onClick={() => void loadZhihu(generationRef.current)} className="text-xs text-blue-700 underline cursor-pointer">{t('retry')}</button>}
              </div>
            )}
          </div>
          <p className="text-xs text-[var(--color-ink-soft)] text-center font-medium max-w-xs">{platform === 'douyin' ? (dyMessage || t('scanWithDouyinApp')) : platform === 'bilibili' ? biliMessage : zhihuMessage}</p>
          <div className="w-full flex flex-col gap-1.5 text-xs text-[var(--color-ink-soft)] bg-black/[0.02] p-3 rounded-xl border border-black/5">
            {instructions.map((text, index) => <div key={text} className="flex gap-2"><span className="text-accent font-bold">{index + 1}.</span>{text}</div>)}
          </div>
          {platform === 'douyin' && (dyStatus === 'pending' || dyStatus === 'loading') && <button type="button" onClick={async () => { try { await api.douyinShowWindow(); setDyWindowOpened(true); } catch (error) { console.warn('Show browser failed:', error); } }} className="text-[11px] text-[var(--color-ink-muted)] hover:text-accent transition-colors underline cursor-pointer -mt-1">{dyWindowOpened ? t('windowOpened') : t('needCaptchaOpenWindow')}</button>}
        </>
      )}
      <button type="button" onClick={() => void handleClose()} className="text-xs text-[var(--color-ink-muted)] hover:text-red-500 transition-colors px-4 py-1.5 rounded-lg hover:bg-black/5 cursor-pointer mt-1">{allPlatformsLoggedIn || dyStatus === 'success' || dyStatus === 'syncing' || biliStatus === 'success' || zhihuStatus === 'logged_in' ? t('close') : t('cancelLogin')}</button>
    </Dialog>
  );
}
