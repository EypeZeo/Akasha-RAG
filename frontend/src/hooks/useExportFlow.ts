import { useCallback, useEffect, useRef, useState } from 'react';
import * as api from '../api';

const ACTIVE_EXPORT_KEY = 'akasha:active_export';

export interface ExportTaskState {
  id: string;
  mode: 'local' | 'browser';
  status: 'queued' | 'running' | 'done' | 'failed';
  progress: number;
  total: number;
  message: string;
  result?: any;
}

type TFunction = (key: string, params?: Record<string, string | number>) => string;

/** Batch-export polling + local-storage restore, extracted from SourcesPanel. */
export function useExportFlow(t: TFunction) {
  const [showExportModal, setShowExportModal] = useState(false);
  const [exportTask, setExportTask] = useState<ExportTaskState | null>(null);
  const exportPollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const exportDownloadedRef = useRef<string | null>(null);
  const mountedRef = useRef(true);
  const generationRef = useRef(0);
  const pollInFlightRef = useRef<number | null>(null);
  const translateRef = useRef(t);
  translateRef.current = t;

  const stopExportPoll = useCallback(() => {
    if (exportPollRef.current) {
      clearInterval(exportPollRef.current);
      exportPollRef.current = null;
    }
  }, []);

  const triggerBrowserDownload = useCallback((taskId: string) => {
    if (exportDownloadedRef.current === taskId) return;
    exportDownloadedRef.current = taskId;
    const a = document.createElement('a');
    a.href = api.exportDownloadUrl(taskId);
    a.download = '';
    document.body.appendChild(a);
    a.click();
    a.remove();
  }, []);

  const pollExportOnce = useCallback(async (
    taskId: string,
    mode: 'local' | 'browser',
    generation: number,
    autoDownload = true,
  ) => {
    // A slow response must not be perpetually superseded by newer interval
    // ticks. Keep at most one request in flight for each task generation.
    if (pollInFlightRef.current === generation) return;
    pollInFlightRef.current = generation;
    try {
      const p = await api.getExportProgress(taskId);
      if (!mountedRef.current || generationRef.current !== generation) return;
      if (!p || p.success === false) {
        // 任务已过期/不存在
        stopExportPoll();
        setExportTask(null);
        try {
          localStorage.removeItem(ACTIVE_EXPORT_KEY);
        } catch { /* ignore */ }
        return;
      }
      setExportTask({
        id: taskId,
        mode,
        status: (p.status as ExportTaskState['status']) || 'running',
        progress: p.progress || 0,
        total: p.total || 0,
        message: translateRef.current('exporting'),
        result: p.result,
      });
      if (p.status === 'done' || p.status === 'failed') {
        stopExportPoll();
        if (p.status === 'done' && mode === 'browser' && autoDownload) triggerBrowserDownload(taskId);
        if (p.status === 'failed') console.error('Export failed:', p.message);
      }
    } catch { /* 网络抖动，下次再试 */ }
    finally {
      if (pollInFlightRef.current === generation) pollInFlightRef.current = null;
    }
  }, [stopExportPoll, triggerBrowserDownload]);

  const startExportPolling = useCallback((taskId: string, mode: 'local' | 'browser') => {
    if (!mountedRef.current) {
      try { localStorage.setItem(ACTIVE_EXPORT_KEY, JSON.stringify({ id: taskId, mode })); } catch { /* ignore */ }
      return;
    }
    stopExportPoll();
    const generation = ++generationRef.current;
    exportDownloadedRef.current = null;
    setExportTask({ id: taskId, mode, status: 'queued', progress: 0, total: 0, message: translateRef.current('exportQueued') });
    try { localStorage.setItem(ACTIVE_EXPORT_KEY, JSON.stringify({ id: taskId, mode })); } catch { /* ignore */ }
    pollExportOnce(taskId, mode, generation);
    exportPollRef.current = setInterval(() => pollExportOnce(taskId, mode, generation), 1500);
  }, [stopExportPoll, pollExportOnce]);

  const dismissExportCard = useCallback(() => {
    generationRef.current += 1;
    stopExportPoll();
    setExportTask(null);
    try {
      localStorage.removeItem(ACTIVE_EXPORT_KEY);
    } catch { /* ignore */ }
  }, [stopExportPoll]);

  const openExportModal = useCallback(() => setShowExportModal(true), []);
  const closeExportModal = useCallback(() => setShowExportModal(false), []);

  // F5 刷新后恢复未完成的导出任务
  useEffect(() => {
    mountedRef.current = true;
    let saved: { id: string; mode: 'local' | 'browser' } | null = null;
    try {
      const raw = localStorage.getItem(ACTIVE_EXPORT_KEY);
      if (raw) {
        const parsed = JSON.parse(raw);
        if (parsed && typeof parsed === 'object' && typeof parsed.id === 'string' && parsed.id &&
            (parsed.mode === 'local' || parsed.mode === 'browser')) {
          saved = { id: parsed.id, mode: parsed.mode };
        } else {
          localStorage.removeItem(ACTIVE_EXPORT_KEY);
        }
      }
    } catch {
      try { localStorage.removeItem(ACTIVE_EXPORT_KEY); } catch { /* ignore */ }
    }
    if (saved?.id) {
      const generation = ++generationRef.current;
      // Restored browser tasks never auto-download. Keep the download marker
      // clear so the explicit Download button still works exactly once.
      exportDownloadedRef.current = null;
      pollExportOnce(saved.id, saved.mode, generation, false);
      exportPollRef.current = setInterval(() => pollExportOnce(saved!.id, saved!.mode, generation, false), 1500);
    }
    return () => {
      mountedRef.current = false;
      generationRef.current += 1;
      stopExportPoll();
    };
  }, [pollExportOnce, stopExportPoll]);

  return {
    exportTask,
    showExportModal,
    openExportModal,
    closeExportModal,
    startExportPolling,
    dismissExportCard,
    triggerBrowserDownload,
  };
}
