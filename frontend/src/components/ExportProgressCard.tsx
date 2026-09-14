import { useI18n } from '../i18n';
import * as api from '../api';
import type { ExportTaskState } from '../hooks/useExportFlow';

/** Batch-export progress card, shown while an export is queued/running/done/failed. */
export default function ExportProgressCard({
  exportTask,
  onDismiss,
  onDownload,
}: {
  exportTask: ExportTaskState | null;
  onDismiss: () => void;
  onDownload: (taskId: string) => void;
}) {
  const { t } = useI18n();

  if (!exportTask) return null;

  return (
    <div className="flex flex-col gap-2 p-3 rounded-xl border border-accent/30 bg-accent-light/60">
      <div className="flex items-center justify-between">
        <span className="text-xs font-semibold text-accent">
          📤 {exportTask.status === 'done' ? t('exportDone') : exportTask.status === 'failed' ? t('exportFailed') : t('exportProgress')}
        </span>
        <button
          onClick={onDismiss}
          className="text-[11px] text-[var(--color-ink-muted)] hover:text-[var(--color-ink)] cursor-pointer"
          title={t('close')}
        >
          ✕
        </button>
      </div>

      {exportTask.status !== 'done' && exportTask.status !== 'failed' && (
        <>
          <p className="text-[11px] text-[var(--color-ink)] truncate">{exportTask.message}</p>
          <div className="h-2 rounded-full bg-black/5 overflow-hidden">
            <div
              className="h-full rounded-full bg-gradient-to-r from-accent to-amber transition-all duration-500"
              style={{ width: `${exportTask.total ? (exportTask.progress / exportTask.total) * 100 : 15}%` }}
            />
          </div>
          {exportTask.total > 0 && (
            <div className="text-[11px] text-[var(--color-ink-soft)] font-medium">
              {exportTask.progress} / {exportTask.total}
            </div>
          )}
        </>
      )}

      {exportTask.status === 'done' && (
        <div className="flex flex-col gap-1.5">
          <p className="text-[11px] text-[var(--color-ink-soft)]">
            {exportTask.mode === 'local'
              ? (exportTask.result?.message || t('savedToLocalDir'))
              : t('fileGeneratedCanDownload')}
          </p>
          <div className="flex gap-1.5">
            {exportTask.mode === 'browser' ? (
              <button
                onClick={() => onDownload(exportTask.id)}
                className="flex-1 py-1.5 rounded-lg text-[11px] font-medium bg-accent text-white hover:opacity-90 cursor-pointer"
              >
                {t('downloadOrRedownload')}
              </button>
            ) : (
              <button
                onClick={() => {
                  const dir = exportTask.result?.target_dir;
                  if (dir) api.openLocalFolder('custom', dir).catch(() => {});
                }}
                className="flex-1 py-1.5 rounded-lg text-[11px] font-medium bg-accent text-white hover:opacity-90 cursor-pointer"
              >
                {t('openFolder')}
              </button>
            )}
            <button
              onClick={onDismiss}
              className="px-3 py-1.5 rounded-lg text-[11px] text-[var(--color-ink-soft)] border border-[var(--color-border)] hover:bg-black/5 cursor-pointer"
            >
              {t('close')}
            </button>
          </div>
        </div>
      )}

      {exportTask.status === 'failed' && (
        <p className="text-[11px] text-red-600 break-all">{t('exportFailedRetry')}</p>
      )}
    </div>
  );
}
