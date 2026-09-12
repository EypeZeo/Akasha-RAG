import { useState, useEffect, useCallback, lazy, Suspense } from 'react';
import LandingPage from './pages/LandingPage';
import LoginModal from './components/LoginModal';
import * as api from './api';

// Landing is the common cold-start route.  Keep the full workspace out of its
// module graph, but avoid fine-grained chunks that are brittle on local update.
const Workspace = lazy(() => import('./pages/Workspace'));

export default function App() {
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
    checkLoginStatus();
    const timer = setInterval(checkLoginStatus, loggedIn ? 30000 : 5000);
    return () => clearInterval(timer);
  }, [checkLoginStatus, loggedIn]);

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
      alert('启动登录服务异常: ' + (e.message || '请检查后端服务'));
    } finally {
      setLoginBusy(false);
    }
  }, [loginBusy]);

  const handleLoginSuccess = useCallback(() => {
    setShowLogin(false);
    setLoggedIn(true);
  }, []);

  const handleLogout = useCallback(async () => {
    try {
      const result = await api.logoutAll();
      if (!result.success) {
        alert('部分平台退出失败，请在设置中重试。');
      }
    } catch (error: any) {
      alert('退出登录失败: ' + (error.message || '请检查后端服务'));
      return;
    }
    await checkLoginStatus();
  }, [checkLoginStatus]);

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
      <Workspace onLogout={handleLogout} onAccountsChanged={checkLoginStatus} />
    </Suspense>
  );
}
