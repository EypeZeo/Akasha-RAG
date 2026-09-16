import { useId, useState } from 'react';
import * as api from '../api';
import { useI18n } from '../i18n';
import Dialog from './ui/Dialog';

const EXPORT_DIR_KEY = 'akasha:exportDir';

interface Props {
  onClose: () => void;
  onExportStarted: (taskId: string, mode: 'local' | 'browser') => void;
  collectionId: string;
  collectionTitle: string;
  doneCount: number;
}

export default function ExportModal({ onClose, onExportStarted, collectionId, collectionTitle, doneCount }: Props) {
  const { t } = useI18n();
  const titleId = useId();
  const [scope, setScope] = useState<'current' | 'all'>('current');
  const [contentType, setContentType] = useState<'both' | 'ai' | 'original'>('both');
  const [format, setFormat] = useState<'markdown' | 'word' | 'excel' | 'ppt' | 'pdf'>('word');
  const [packMode, setPackMode] = useState<'single' | 'zip'>('single');
  const [destType, setDestType] = useState<'browser' | 'local'>('local');
  const [targetDir, setTargetDir] = useState<string>(() => {
    try { return localStorage.getItem(EXPORT_DIR_KEY) || ''; } catch { return ''; }
  });
  const [autoOpen, setAutoOpen] = useState<boolean>(true);
  const [exporting, setExporting] = useState(false);
  const [picking, setPicking] = useState(false);
  const [error, setError] = useState('');

  const handlePickDir = async () => {
    setPicking(true);
    setError('');
    try {
      const res = await api.pickDirectory(targetDir || undefined);
      if (res.success && res.path) {
        setTargetDir(res.path);
        try { localStorage.setItem(EXPORT_DIR_KEY, res.path); } catch { /* ignore */ }
      } else if (res.busy) {
        setError(t('exportPickWait'));
      } else if (res.message) {
        console.error('Directory picker failed:', res.message);
        setError(t('exportFailedRetry'));
      }
    } catch (e: any) {
      console.error('Directory picker failed:', e);
      setError(t('exportOpenDirFailed'));
    } finally {
      setPicking(false);
    }
  };

  const handleExport = async () => {
    if (destType === 'local' && !targetDir.trim()) {
      setError(t('exportSelectDirFirst'));
      return;
    }
    setExporting(true);
    setError('');
    try {
      const res = await api.exportBatchStart({
        collection_id: scope === 'current' && collectionId !== 'all' ? collectionId : null,
        content_type: contentType,
        format: format,
        pack_mode: packMode,
        target_dir: destType === 'local' ? targetDir.trim() : null,
        auto_open: autoOpen,
      });
      if (destType === 'local') {
        try { localStorage.setItem(EXPORT_DIR_KEY, targetDir.trim()); } catch { /* ignore */ }
      }
      onExportStarted(res.task_id, res.mode);
      onClose();
    } catch (e: any) {
      console.error('Export submission failed:', e);
      setError(t('exportFailedRetry'));
    } finally {
      setExporting(false);
    }
  };

  return (
    <Dialog
      onClose={onClose}
      labelledBy={titleId}
      closeOnBackdropClick={!exporting}
      closeOnEscape={!exporting}
      className="bg-[var(--color-panel)] rounded-2xl w-full max-w-lg shadow-2xl flex flex-col border border-[var(--color-border)] overflow-hidden"
    >
        {/* Header */}
        <div className="px-6 py-4 border-b border-[var(--color-border)] flex items-center justify-between bg-white/70">
          <div className="flex items-center gap-2">
            <span className="text-xl">📦</span>
            <h2 id={titleId} className="text-base font-bold text-[var(--color-ink)]">{t('exportModalTitle')}</h2>
          </div>
          <button onClick={onClose} aria-label={t('close')} className="text-sm text-[var(--color-ink-muted)] hover:text-[var(--color-ink)] p-1 rounded-lg">✕</button>
        </div>

        {/* Body */}
        <div className="p-6 flex flex-col gap-4 overflow-y-auto max-h-[72vh]">
          {error && (
            <div className="p-3 bg-red-50 text-red-600 rounded-xl text-xs border border-red-200">
              {error}
            </div>
          )}

          {/* 1. 范围 */}
          <div>
            <label className="text-xs font-semibold text-[var(--color-ink-soft)] block mb-1.5">{t('exportStepScope')}</label>
            <div className="grid grid-cols-2 gap-2">
              <button
                type="button"
                onClick={() => setScope('current')}
                className={`p-2.5 rounded-xl border text-left text-xs transition-all ${
                  scope === 'current'
                    ? 'border-accent bg-accent/5 font-semibold text-accent shadow-xs'
                    : 'border-[var(--color-border)] text-[var(--color-ink-soft)] hover:bg-black/2'
                }`}
              >
                <div>{t('exportScopeSelected')}</div>
                <div className="text-[11px] opacity-70 mt-0.5 truncate">{collectionTitle}</div>
              </button>
              <button
                type="button"
                onClick={() => setScope('all')}
                className={`p-2.5 rounded-xl border text-left text-xs transition-all ${
                  scope === 'all'
                    ? 'border-accent bg-accent/5 font-semibold text-accent shadow-xs'
                    : 'border-[var(--color-border)] text-[var(--color-ink-soft)] hover:bg-black/2'
                }`}
              >
                <div>{t('exportScopeAll')}</div>
                <div className="text-[11px] opacity-70 mt-0.5">{t('exportScopeAllDesc', { count: doneCount })}</div>
              </button>
            </div>
          </div>

          {/* 2. 内容 */}
          <div>
            <label className="text-xs font-semibold text-[var(--color-ink-soft)] block mb-1.5">{t('exportStepContent')}</label>
            <div className="grid grid-cols-3 gap-2">
              {[
                { id: 'both', label: t('exportContentBoth'), desc: t('exportContentBothDesc') },
                { id: 'ai', label: t('exportContentAi'), desc: t('exportContentAiDesc') },
                { id: 'original', label: t('exportContentOriginal'), desc: t('exportContentOriginalDesc') },
              ].map(item => (
                <button
                  key={item.id}
                  type="button"
                  onClick={() => setContentType(item.id as any)}
                  className={`p-2 rounded-xl border text-center transition-all ${
                    contentType === item.id
                      ? 'border-accent bg-accent/5 font-semibold text-accent shadow-xs'
                      : 'border-[var(--color-border)] text-[var(--color-ink-soft)] hover:bg-black/2'
                  }`}
                >
                  <div className="text-xs">{item.label}</div>
                  <div className="text-[10px] opacity-70 mt-0.5">{item.desc}</div>
                </button>
              ))}
            </div>
          </div>

          {/* 3. 格式 */}
          <div>
            <label className="text-xs font-semibold text-[var(--color-ink-soft)] block mb-1.5">{t('exportStepFormat')}</label>
            <div className="grid grid-cols-5 gap-1.5">
              {[
                { id: 'word', label: 'Word', ext: '.docx', icon: '📝' },
                { id: 'excel', label: 'Excel', ext: '.xlsx', icon: '📊' },
                { id: 'markdown', label: 'Markdown', ext: '.md', icon: '📑' },
                { id: 'ppt', label: 'PPT', ext: '.pptx', icon: '📽️' },
                { id: 'pdf', label: 'PDF', ext: '.pdf', icon: '📕' },
              ].map(f => (
                <button
                  key={f.id}
                  type="button"
                  onClick={() => setFormat(f.id as any)}
                  className={`p-2 rounded-xl border text-center transition-all ${
                    format === f.id
                      ? 'border-accent bg-accent/5 font-semibold text-accent shadow-xs'
                      : 'border-[var(--color-border)] text-[var(--color-ink-soft)] hover:bg-black/2'
                  }`}
                >
                  <div className="text-base mb-0.5">{f.icon}</div>
                  <div className="text-xs">{f.label}</div>
                  <div className="text-[10px] opacity-60 font-mono">{f.ext}</div>
                </button>
              ))}
            </div>
          </div>

          {/* 4. 单文件 vs 多文件 */}
          <div>
            <label className="text-xs font-semibold text-[var(--color-ink-soft)] block mb-1.5">{t('exportStepPack')}</label>
            <div className="grid grid-cols-2 gap-2">
              <label className={`p-2.5 rounded-xl border flex items-center gap-2 cursor-pointer transition-all ${
                packMode === 'single' ? 'border-accent bg-accent/5 font-medium text-accent' : 'border-[var(--color-border)] text-[var(--color-ink-soft)]'
              }`}>
                <input
                  type="radio"
                  name="packMode"
                  value="single"
                  checked={packMode === 'single'}
                  onChange={() => setPackMode('single')}
                  className="accent-accent"
                />
                <div>
                  <div className="text-xs">{t('packSingle')}</div>
                  <div className="text-[10px] opacity-70">{t('packSingleDesc')}</div>
                </div>
              </label>

              <label className={`p-2.5 rounded-xl border flex items-center gap-2 cursor-pointer transition-all ${
                packMode === 'zip' ? 'border-accent bg-accent/5 font-medium text-accent' : 'border-[var(--color-border)] text-[var(--color-ink-soft)]'
              }`}>
                <input
                  type="radio"
                  name="packMode"
                  value="zip"
                  checked={packMode === 'zip'}
                  onChange={() => setPackMode('zip')}
                  className="accent-accent"
                />
                <div>
                  <div className="text-xs">{t('packZip')}</div>
                  <div className="text-[10px] opacity-70">{t('packZipDesc')}</div>
                </div>
              </label>
            </div>
          </div>

          {/* 5. 导出保存位置 */}
          <div>
            <label className="text-xs font-semibold text-[var(--color-ink-soft)] block mb-1.5">{t('exportStepDest')}</label>
            <div className="flex gap-2 mb-2">
              <button
                type="button"
                onClick={() => setDestType('local')}
                className={`flex-1 py-1.5 px-3 rounded-lg border text-xs font-medium transition-all ${
                  destType === 'local' ? 'bg-accent text-white border-accent shadow-xs' : 'bg-white border-[var(--color-border)] text-[var(--color-ink-soft)]'
                }`}
              >
                {t('destLocal')}
              </button>
              <button
                type="button"
                onClick={() => setDestType('browser')}
                className={`py-1.5 px-3 rounded-lg border text-xs font-medium transition-all ${
                  destType === 'browser' ? 'bg-accent text-white border-accent shadow-xs' : 'bg-white border-[var(--color-border)] text-[var(--color-ink-soft)]'
                }`}
              >
                {t('destBrowser')}
              </button>
            </div>

            {destType === 'local' && (
              <div className="p-3 bg-black/[0.02] border border-[var(--color-border)] rounded-xl flex flex-col gap-2">
                <div>
                  <span className="text-[11px] text-[var(--color-ink-muted)] block mb-1">{t('targetDirLabel')}</span>
                  <div className="flex gap-1.5">
                    <input
                      type="text"
                      value={targetDir}
                      onChange={e => setTargetDir(e.target.value)}
                      placeholder={t('targetDirPlaceholder')}
                      className="flex-1 min-w-0 px-3 py-1.5 text-xs bg-white border border-[var(--color-border)] rounded-lg outline-none focus:border-accent font-mono"
                    />
                    <button
                      type="button"
                      onClick={handlePickDir}
                      disabled={picking}
                      className="flex-shrink-0 px-3 py-1.5 text-xs rounded-lg border border-[var(--color-border)] bg-white hover:border-accent hover:text-accent transition-colors disabled:opacity-50 cursor-pointer"
                    >
                      {picking ? '⏳' : t('browse')}
                    </button>
                  </div>
                </div>
                <label className="flex items-center gap-2 text-xs text-[var(--color-ink-soft)] cursor-pointer mt-0.5">
                  <input
                    type="checkbox"
                    checked={autoOpen}
                    onChange={e => setAutoOpen(e.target.checked)}
                    className="accent-accent"
                  />
                  <span>{t('autoOpenLabel')}</span>
                </label>
              </div>
            )}
          </div>
        </div>

        {/* Footer */}
        <div className="px-6 py-4 border-t border-[var(--color-border)] bg-black/[0.02] flex items-center justify-end gap-3">
          <button
            type="button"
            onClick={onClose}
            disabled={exporting}
            className="px-4 py-2 rounded-xl text-xs text-[var(--color-ink-soft)] hover:bg-black/5 transition-colors disabled:opacity-50 cursor-pointer"
          >
            {t('cancel')}
          </button>
          <button
            type="button"
            onClick={handleExport}
            disabled={exporting}
            className="px-5 py-2.5 rounded-xl text-xs font-bold text-white bg-gradient-to-r from-accent to-amber hover:opacity-90 transition-all disabled:opacity-50 flex items-center gap-1.5 shadow-md shadow-accent/20 cursor-pointer"
          >
            {exporting ? (
              <>
                <span className="animate-spin text-sm">⏳</span>
                <span>{t('submitting')}</span>
              </>
            ) : (
              <>
                <span>🚀</span>
                <span>{destType === 'local' ? t('startExportLocal') : t('startExportBrowser')}</span>
              </>
            )}
          </button>
        </div>
    </Dialog>
  );
}
