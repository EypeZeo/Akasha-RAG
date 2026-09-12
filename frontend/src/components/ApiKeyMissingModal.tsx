import { useI18n } from '../i18n';

interface Props {
  /** which action was blocked — picks the right message */
  kind: 'chat' | 'ingest';
  onClose: () => void;
  onOpenSettings: () => void;
}

/**
 * Soft API-key gate: shown instead of letting a chat message or a build
 * request fail deep inside the pipeline when no usable key is configured yet.
 * Visual pattern copied from Workspace.tsx's logout-confirm modal.
 */
export default function ApiKeyMissingModal({ kind, onClose, onOpenSettings }: Props) {
  const { t } = useI18n();

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/45 backdrop-blur-xs p-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-sm rounded-2xl bg-white p-6 shadow-2xl border border-[var(--color-border)]"
        onClick={event => event.stopPropagation()}
      >
        <h2 className="text-base font-bold text-[var(--color-ink)]">{t('apiKeyMissingTitle')}</h2>
        <p className="mt-2 text-xs leading-5 text-[var(--color-ink-soft)]">
          {kind === 'chat' ? t('apiKeyMissingChatDesc') : t('apiKeyMissingIngestDesc')}
        </p>
        <div className="mt-5 flex justify-end gap-2">
          <button
            onClick={onClose}
            className="px-3 py-1.5 text-xs rounded-lg border border-[var(--color-border)] text-[var(--color-ink-soft)] cursor-pointer"
          >
            {t('cancel')}
          </button>
          <button
            onClick={() => { onClose(); onOpenSettings(); }}
            className="px-3 py-1.5 text-xs rounded-lg bg-gradient-to-r from-accent to-accent-hover text-white cursor-pointer"
          >
            {t('apiKeyMissingGoToSettings')}
          </button>
        </div>
      </div>
    </div>
  );
}
