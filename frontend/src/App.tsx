import { useState, useEffect, useCallback, lazy, Suspense } from 'react';
import LandingPage from './pages/LandingPage';
import LoginModal from './components/LoginModal';
import * as api from './api';
import { useI18n } from './i18n';
import { useThemeSetting } from './utils/settings';

// Landing is the common cold-start route.  Keep the full workspace out of its
// module graph, but avoid fine-grained chunks that are brittle on local update.
const Workspace = lazy(() => import('./pages/Workspace'));

export default function App() {
  const { t } = useI18n();
  const [theme, setTheme] = useThemeSetting();
  const [loggedIn, setLoggedIn] = useState(false);
  const [showLogin, setShowLogin] = useState(false);
  const [loginBusy, setLoginBusy] = useState(false);

  const checkLoginStatus = useCallback(async () => {
      try {
        const pRes = await api.listPlatforms();
        if (pRes.success && pRes.platforms) {
          const anyLoggedIn = pRes.platforms.some(p => p.is_logged_in);
          setLoggedIn(anyLoggedIn);
          return;
        }
      } catch { /* backend not ready or fallback */ }
      try {
        const s = await api.loginStatus();
        setLoggedIn(s.status === 'logged_in');
      } catch { /* backend not ready */ }
  }, []);

  // Poll login status
  useEffect(() => {
    // LoginModal performs its own short-interval QR polling. Avoid a second
    // platform-status request stream while it is open; otherwise idle polling
    // is deliberately slow to keep the local backend quiet.
    if (showLogin) return;
    checkLoginStatus();
    const timer = setInterval(checkLoginStatus, 30000);
    return () => clearInterval(timer);
  }, [checkLoginStatus, showLogin]);

  const handleLogin = useCallback(async () => {
    if (loginBusy) return;
    setLoginBusy(true);
    try {
      // 先查询各平台状态：如果有已登录的，直接进入
      try {
        const pRes = await api.listPlatforms();
        if (pRes.success && pRes.platforms.some(p => p.is_logged_in)) {
          setLoggedIn(true);
          setLoginBusy(false);
          return;
        }
      } catch {}

      setShowLogin(true);
    } catch (e: any) {
      console.error(e);
      alert(t('loginServiceStartFailed'));
    } finally {
      setLoginBusy(false);
    }
  }, [loginBusy, t]);

  const handleLoginSuccess = useCallback(() => {
    setShowLogin(false);
    setLoggedIn(true);
  }, []);

  const handleLogout = useCallback(async () => {
    try {
      const result = await api.logoutAll();
      if (!result.success) {
        alert(t('partialLogoutFailed'));
      }
    } catch (error: any) {
      console.error(error);
      alert(t('operationFailed'));
      return;
    }
    await checkLoginStatus();
  }, [checkLoginStatus, t]);

  if (!loggedIn) {
    return (
      <>
        <LandingPage onStartLogin={handleLogin} busy={loginBusy} />
        {showLogin && (
          <LoginModal
            onClose={() => {
              setShowLogin(false);
              checkLoginStatus();
            }}
            onSuccess={handleLoginSuccess}
          />
        )}
      </>
    );
  }

  return (
    <Suspense fallback={<LandingPage onStartLogin={() => {}} busy />}>
      <Workspace onLogout={handleLogout} onAccountsChanged={checkLoginStatus} theme={theme} onThemeChange={setTheme} />
    </Suspense>
  );
}
