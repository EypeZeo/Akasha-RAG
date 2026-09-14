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

  const pollExportOnce = useCallback(async (taskId: string, mode: 'local' | 'browser') => {
    try {
      const p = await api.getExportProgress(taskId);
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
        message: t('exporting'),
        result: p.result,
      });
      if (p.status === 'done' || p.status === 'failed') {
        stopExportPoll();
        if (p.status === 'done' && mode === 'browser') triggerBrowserDownload(taskId);
        if (p.status === 'failed') console.error('Export failed:', p.message);
      }
    } catch { /* 网络抖动，下次再试 */ }
  }, [stopExportPoll, triggerBrowserDownload, t]);

  const startExportPolling = useCallback((taskId: string, mode: 'local' | 'browser') => {
    stopExportPoll();
    exportDownloadedRef.current = null;
    setExportTask({ id: taskId, mode, status: 'queued', progress: 0, total: 0, message: t('exportQueued') });
    try { localStorage.setItem(ACTIVE_EXPORT_KEY, JSON.stringify({ id: taskId, mode })); } catch { /* ignore */ }
    pollExportOnce(taskId, mode);
    exportPollRef.current = setInterval(() => pollExportOnce(taskId, mode), 1500);
  }, [stopExportPoll, pollExportOnce, t]);

  const dismissExportCard = useCallback(() => {
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
    let saved: { id: string; mode: 'local' | 'browser' } | null = null;
    try {
      const raw = localStorage.getItem(ACTIVE_EXPORT_KEY);
      if (raw) saved = JSON.parse(raw);
    } catch { /* ignore */ }
    if (saved?.id) {
      exportDownloadedRef.current = saved.id; // 恢复时不自动重下，交给用户点按钮
      pollExportOnce(saved.id, saved.mode);
      exportPollRef.current = setInterval(() => pollExportOnce(saved!.id, saved!.mode), 1500);
    }
    return () => stopExportPoll();
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
