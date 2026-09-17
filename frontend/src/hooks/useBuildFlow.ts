import { useCallback, useEffect, useRef, useState } from 'react';
import * as api from '../api';

const ACTIVE_BUILD_KEY = 'akasha:active_build';

type TFunction = (key: string, params?: Record<string, string | number>) => string;

/** Build/ingest polling + local-storage restore, extracted from SourcesPanel. */
export function useBuildFlow(t: TFunction, onBuildComplete: () => void) {
  const [building, setBuilding] = useState(false);
  const [buildProgress, setBuildProgress] = useState(0);
  const [buildTotal, setBuildTotal] = useState(0);
  const [buildMessage, setBuildMessage] = useState('');
  const [buildTaskId, setBuildTaskId] = useState<string | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const buildPollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const buildMissesRef = useRef(0);

  const stopBuildPoll = useCallback(() => {
    if (buildPollRef.current) {
      clearInterval(buildPollRef.current);
      buildPollRef.current = null;
    }
  }, []);

  const finishBuild = useCallback(() => {
    stopBuildPoll();
    setBuilding(false);
    setBuildTaskId(null);
    setCancelling(false);
    try {
      localStorage.removeItem(ACTIVE_BUILD_KEY);
    } catch { /* ignore */ }
  }, [stopBuildPoll]);

  const startBuildPolling = useCallback((taskId: string, typeLabel: string, restoring = false) => {
    stopBuildPoll();
    buildMissesRef.current = 0;
    setBuilding(true);
    setBuildTaskId(taskId);
    if (!restoring) setBuildProgress(0);
    setBuildMessage(restoring ? t('ingestRestoring') : t('ingestStarting', { type: typeLabel }));
    try {
      localStorage.setItem(ACTIVE_BUILD_KEY, JSON.stringify({ task_id: taskId, typeLabel }));
    } catch { /* ignore */ }

    const tick = async () => {
      let p: any;
      try {
        p = await api.getSyncProgress(taskId);
      } catch {
        return; // 网络抖动，下次再试
      }
      if (!p || p.success === false) {
        // 后端重启 / 任务过期：自愈复位，绝不永久卡住
        buildMissesRef.current += 1;
        if (buildMissesRef.current >= 2) finishBuild();
        return;
      }
      buildMissesRef.current = 0;
      setBuildProgress(p.progress || 0);
      if (p.total) setBuildTotal(p.total);
      if (p.message) setBuildMessage(t('ingesting'));
      if (p.status === 'done' || p.status === 'failed' || p.status === 'cancelled') {
        stopBuildPoll();
        if (p.status === 'done') {
          setBuildProgress(p.total || 0);
          setBuildMessage(t('ingestCompleted', { type: typeLabel }));
        } else if (p.status === 'cancelled') {
          setBuildMessage(t('ingestCancelled'));
        }
        setTimeout(() => {
          finishBuild();
          onBuildComplete();
        }, 900);
      }
    };
    tick();
    buildPollRef.current = setInterval(tick, 1500);
  }, [stopBuildPoll, finishBuild, onBuildComplete, t]);

  const setBuildTotalHint = useCallback((n: number) => setBuildTotal(n), []);

  // F5 刷新后恢复未完成的入库任务
  useEffect(() => {
    let saved: { task_id: string; typeLabel: string } | null = null;
    try {
      const raw = localStorage.getItem(ACTIVE_BUILD_KEY);
      if (raw) saved = JSON.parse(raw);
    } catch { /* ignore */ }
    if (saved?.task_id) startBuildPolling(saved.task_id, saved.typeLabel || t('categoryContent'), true);
    return () => stopBuildPoll();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [startBuildPolling, t]);

  const handleCancelBuild = async () => {
    if (!buildTaskId || cancelling) return;
    setCancelling(true);
    try {
      await api.cancelSync(buildTaskId);
      setBuildMessage(t('cancelling'));
    } catch (e: any) {
      setCancelling(false);
      console.error('Cancel ingest failed:', e);
      alert(t('operationFailed'));
    }
  };

  return {
    building,
    buildProgress,
    buildTotal,
    buildMessage,
    buildTaskId,
    cancelling,
    startBuildPolling,
    setBuildTotalHint,
    handleCancelBuild,
  };
}
