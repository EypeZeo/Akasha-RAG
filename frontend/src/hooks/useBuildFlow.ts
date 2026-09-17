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
  // "跨代"正确性：新任务开始/finishBuild()/卸载时递增，每个 tick 闭包记住
  // 自己创建时的值，真正生效前重新比对——过期任务的响应、组件卸载后的迟到
  // 响应都会在这一步被丢弃。
  const generationRef = useRef(0);
  // "同代内"正确性：generationRef 管不到同一个任务的两次 tick 意外重叠都
  // 拿到终态响应的情况（两者的 generation 相同）。每次 startBuildPolling
  // 开始新任务时重置为 false；谁先观察到终态谁把它置为 true 并继续收尾，
  // 后到的直接放弃，确保收尾副作用只会真正执行一次。
  const terminalSeizedRef = useRef(false);
  // 记录 900ms 收尾定时器的句柄，便于开始新任务/卸载时主动 clearTimeout，
  // 不让它在"它所属的那次收尾"已经不再作数之后还独立触发。
  const terminalTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // syncKnowledge 仍在等待响应时组件被卸载，成功响应落地后 startBuildPolling
  // 仍会被调用（handleBuild 是普通闭包，不受 React 卸载保护）——mountedRef
  // 让这次调用只持久化 ACTIVE_BUILD_KEY，不建立轮询、不写 state。
  const mountedRef = useRef(true);
  // 是否有一轮构建正在进行（从 startBuildPolling 真正开始到 finishBuild
  // 之间，含终态待收尾窗口）。特意用 ref 而不是读 `building` state——
  // F5 恢复 effect 需要在自己重跑时同步查询"现在是不是已经在构建"，如果
  // 把 `building` 直接加进这个 effect 的依赖数组，`startBuildPolling` 自己
  // 触发的 `setBuilding(true)` 会让 effect 立即重跑一次、先执行上一轮的
  // cleanup（`stopBuildPoll()`），把刚建立的轮询定时器直接清掉——用 ref
  // 读取当前状态，不把它变成依赖项，就不会有这个连带效应。
  const activeRef = useRef(false);

  const clearTerminalTimeout = useCallback(() => {
    if (terminalTimeoutRef.current) {
      clearTimeout(terminalTimeoutRef.current);
      terminalTimeoutRef.current = null;
    }
  }, []);

  const stopBuildPoll = useCallback(() => {
    if (buildPollRef.current) {
      clearInterval(buildPollRef.current);
      buildPollRef.current = null;
    }
  }, []);

  const finishBuild = useCallback(() => {
    stopBuildPoll();
    clearTerminalTimeout();
    generationRef.current += 1;
    activeRef.current = false;
    setBuilding(false);
    setBuildTaskId(null);
    setCancelling(false);
    try {
      localStorage.removeItem(ACTIVE_BUILD_KEY);
    } catch { /* ignore */ }
  }, [stopBuildPoll, clearTerminalTimeout]);

  const startBuildPolling = useCallback((taskId: string, typeLabel: string, restoring = false) => {
    if (!mountedRef.current) {
      // 提交阶段的响应在卸载后才落地：只把 task_id 持久化下来，不建立
      // 轮询、不写 state——下次挂载时现成的 F5 恢复 effect 会接管它，
      // 而不是让后端任务变成前端完全没有记录的孤儿。
      try {
        localStorage.setItem(ACTIVE_BUILD_KEY, JSON.stringify({ task_id: taskId, typeLabel }));
      } catch { /* ignore */ }
      return;
    }
    stopBuildPoll();
    clearTerminalTimeout();
    generationRef.current += 1;
    const myGeneration = generationRef.current;
    terminalSeizedRef.current = false;
    activeRef.current = true;
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
      if (generationRef.current !== myGeneration) return; // 过期任务/卸载后的迟到响应，整个丢弃
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
        if (terminalSeizedRef.current) return; // 同代内已有另一次 tick 先一步抢到终态收尾
        terminalSeizedRef.current = true;
        stopBuildPoll();
        if (p.status === 'done') {
          setBuildProgress(p.total || 0);
          setBuildMessage(t('ingestCompleted', { type: typeLabel }));
        } else if (p.status === 'cancelled') {
          setBuildMessage(t('ingestCancelled'));
        }
        terminalTimeoutRef.current = setTimeout(() => {
          terminalTimeoutRef.current = null;
          finishBuild();
          onBuildComplete();
        }, 900);
      }
    };
    tick();
    buildPollRef.current = setInterval(tick, 1500);
  }, [stopBuildPoll, clearTerminalTimeout, finishBuild, onBuildComplete, t]);

  const setBuildTotalHint = useCallback((n: number) => setBuildTotal(n), []);

  // F5 刷新后恢复未完成的入库任务。`startBuildPolling`/`t` 的身份会随
  // `onBuildComplete`（SourcesPanel 里 expandedId/videoPage 等变化时重新
  // 生成）频繁变化，这个 effect 因此比"只在挂载时跑一次"更容易重跑——
  // 如果不守卫，一次正常进行中的构建会被这类无关的重渲染意外重启（重新
  // 走一遍 startBuildPolling，取消刚安排好的终态收尾）。只在"当前确实
  // 没有活跃构建"时才尝试从 localStorage 恢复。
  useEffect(() => {
    if (activeRef.current) return;
    let saved: { task_id: string; typeLabel: string } | null = null;
    try {
      const raw = localStorage.getItem(ACTIVE_BUILD_KEY);
      if (raw !== null) {
        const parsed = JSON.parse(raw);
        const taskId = parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed.task_id : undefined;
        if (typeof taskId === 'string' && taskId.length > 0) {
          saved = { task_id: taskId, typeLabel: typeof parsed.typeLabel === 'string' ? parsed.typeLabel : '' };
        } else {
          // 合法 JSON 但形状不对（task_id 缺失/非字符串/整体是数组等）——
          // 这份残留数据用不了，主动清理，不要留着每次挂载都重新尝试一遍。
          try { localStorage.removeItem(ACTIVE_BUILD_KEY); } catch { /* ignore */ }
        }
      }
    } catch {
      // JSON.parse 抛异常（非法 JSON、空字符串等）——同样是用不了的坏数据，
      // 主动清理，不能只是"这次不用它"就完事,否则会永久残留、每次挂载都
      // 重新触发一次同样的静默失败。
      try { localStorage.removeItem(ACTIVE_BUILD_KEY); } catch { /* ignore */ }
    }
    if (saved?.task_id) startBuildPolling(saved.task_id, saved.typeLabel || t('categoryContent'), true);
    return () => stopBuildPoll();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [startBuildPolling, t]);

  // 只在真正卸载时触发一次（空依赖数组），和上面那个会随 startBuildPolling
  // 身份变化而重跑的 F5 恢复 effect 分开——避免把"重跑"误当成"卸载"。
  useEffect(() => {
    return () => {
      mountedRef.current = false;
      generationRef.current += 1;
    };
  }, []);

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
