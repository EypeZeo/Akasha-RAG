import { useState, useEffect, useCallback, useRef } from 'react';
import * as api from '../api';
import { useI18n } from '../i18n';
import { useWorkspaceStore } from '../store/workspace';

interface Props {
  /** bumped by the parent when a new session is created / a turn completes */
  refreshKey: number;
  /** called after a session is deleted so the parent can clear its transcript if needed */
  onSessionDeleted: (id: number) => void;
  /** start a brand-new blank conversation */
  onNewChat: () => void;
}

type Bucket = 'today' | 'yesterday' | 'prev7' | 'older';

function bucketOf(iso: string | null): Bucket {
  if (!iso) return 'older';
  const d = new Date(iso.replace(' ', 'T'));
  if (Number.isNaN(d.getTime())) return 'older';
  const now = new Date();
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const t = d.getTime();
  if (t >= startOfToday) return 'today';
  if (t >= startOfToday - 86_400_000) return 'yesterday';
  if (t >= startOfToday - 7 * 86_400_000) return 'prev7';
  return 'older';
}

const BUCKET_ORDER: Bucket[] = ['today', 'yesterday', 'prev7', 'older'];
const BUCKET_KEY: Record<Bucket, string> = {
  today: 'histToday',
  yesterday: 'histYesterday',
  prev7: 'histPrev7Days',
  older: 'histOlder',
};

export default function ChatSessionDrawer({ refreshKey, onSessionDeleted, onNewChat }: Props) {
  const { t } = useI18n();
  const activeSessionId = useWorkspaceStore(s => s.activeSessionId);
  const openSession = useWorkspaceStore(s => s.openSession);

  const [collapsed, setCollapsed] = useState(false);
  const [sessions, setSessions] = useState<api.SessionItem[]>([]);
  const [search, setSearch] = useState('');
  const [debounced, setDebounced] = useState('');
  const [renamingId, setRenamingId] = useState<number | null>(null);
  const [renameValue, setRenameValue] = useState('');
  const renameRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const timer = setTimeout(() => setDebounced(search), 250);
    return () => clearTimeout(timer);
  }, [search]);

  const fetchSessions = useCallback(async () => {
    try {
      const r = await api.listSessions(debounced);
      if (r.success) setSessions(r.items);
    } catch { /* ignore */ }
  }, [debounced]);

  useEffect(() => { fetchSessions(); }, [fetchSessions, refreshKey]);

  useEffect(() => {
    if (renamingId != null) renameRef.current?.focus();
  }, [renamingId]);

  const startRename = (s: api.SessionItem) => {
    setRenamingId(s.id);
    setRenameValue(s.title);
  };

  const commitRename = async () => {
    const id = renamingId;
    const title = renameValue.trim();
    setRenamingId(null);
    if (id == null || !title) return;
    try {
      await api.renameSession(id, title);
      setSessions(prev => prev.map(s => (s.id === id ? { ...s, title } : s)));
    } catch { /* ignore */ }
  };

  const handleDelete = async (id: number) => {
    if (!confirm(t('confirmDeleteChat'))) return;
    try {
      await api.deleteSession(id);
      setSessions(prev => prev.filter(s => s.id !== id));
      onSessionDeleted(id);
    } catch { /* ignore */ }
  };

  const grouped = BUCKET_ORDER.map(b => ({
    bucket: b,
    items: sessions.filter(s => bucketOf(s.last_message_at ?? s.created_at) === b),
  })).filter(g => g.items.length > 0);

  if (collapsed) {
    return (
      <div className="w-11 flex-shrink-0 border-r border-[var(--color-border)] bg-[var(--color-panel)] flex flex-col items-center py-3 gap-2 no-print">
        <button
          type="button"
          onClick={() => setCollapsed(false)}
          title={t('expandDrawer')}
          className="w-8 h-8 rounded-xl hover:bg-black/5 text-[var(--color-ink-muted)] flex items-center justify-center cursor-pointer"
        >
          ☰
        </button>
        <button
          type="button"
          onClick={onNewChat}
          title={t('newChat')}
          className="w-8 h-8 rounded-xl bg-accent text-white flex items-center justify-center cursor-pointer shadow-sm"
        >
          ＋
        </button>
      </div>
    );
  }

  return (
    <div className="w-64 flex-shrink-0 border-r border-[var(--color-border)] bg-[var(--color-panel)] flex flex-col min-h-0 no-print">
      <div className="p-2.5 flex flex-col gap-2 flex-shrink-0">
        <div className="flex items-center gap-1.5">
          <button
            type="button"
            onClick={onNewChat}
            className="flex-1 flex items-center justify-center gap-1.5 px-3 py-2 rounded-xl bg-accent text-white text-xs font-semibold shadow-sm hover:shadow-md transition-all cursor-pointer"
          >
            ＋ {t('newChat')}
          </button>
          <button
            type="button"
            onClick={() => setCollapsed(true)}
            title={t('collapseDrawer')}
            className="w-8 h-8 rounded-xl hover:bg-black/5 text-[var(--color-ink-muted)] flex items-center justify-center cursor-pointer flex-shrink-0"
          >
            ⟨
          </button>
        </div>
        <input
          type="text"
          value={search}
          onChange={e => setSearch(e.target.value)}
          placeholder={t('searchChats')}
          className="w-full text-xs px-2.5 py-1.5 rounded-lg bg-white/70 border border-[var(--color-border)] focus:outline-none focus:border-accent text-[var(--color-ink)]"
        />
      </div>

      <div className="flex-1 overflow-y-auto px-2 pb-3 subtle-scrollbar">
        {sessions.length === 0 ? (
          <p className="text-[11px] text-[var(--color-ink-muted)] text-center py-6">
            {debounced ? t('noMatchedChats') : t('noHistory')}
          </p>
        ) : (
          grouped.map(group => (
            <div key={group.bucket} className="mb-2">
              <div className="px-2 py-1 text-[10px] font-semibold text-[var(--color-ink-muted)] uppercase tracking-wide">
                {t(BUCKET_KEY[group.bucket])}
              </div>
              <div className="flex flex-col gap-0.5">
                {group.items.map(s => {
                  const isActive = activeSessionId === s.id;
                  return (
                    <div
                      key={s.id}
                      onClick={() => renamingId !== s.id && openSession(s.id)}
                      className={`group flex items-center gap-1 px-2 py-1.5 rounded-lg text-xs cursor-pointer transition-colors ${
                        isActive
                          ? 'bg-accent-light text-accent font-semibold'
                          : 'text-[var(--color-ink-soft)] hover:bg-black/5'
                      }`}
                    >
                      {renamingId === s.id ? (
                        <input
                          ref={renameRef}
                          value={renameValue}
                          onChange={e => setRenameValue(e.target.value)}
                          onClick={e => e.stopPropagation()}
                          onKeyDown={e => {
                            if (e.key === 'Enter') commitRename();
                            if (e.key === 'Escape') setRenamingId(null);
                          }}
                          onBlur={commitRename}
                          className="flex-1 min-w-0 text-xs px-1 py-0.5 rounded border border-accent bg-white text-[var(--color-ink)] focus:outline-none"
                        />
                      ) : (
                        <>
                          <span className="flex-1 truncate">{s.title}</span>
                          <span className="text-[9px] text-[var(--color-ink-muted)] opacity-70 flex-shrink-0">
                            {s.message_count}
                          </span>
                          <button
                            type="button"
                            onClick={e => { e.stopPropagation(); startRename(s); }}
                            title={t('renameChat')}
                            className="opacity-0 group-hover:opacity-100 w-5 h-5 rounded hover:bg-black/10 flex items-center justify-center text-[10px] flex-shrink-0 transition-opacity"
                          >
                            ✎
                          </button>
                          <button
                            type="button"
                            onClick={e => { e.stopPropagation(); handleDelete(s.id); }}
                            title={t('deleteChat')}
                            className="opacity-0 group-hover:opacity-100 w-5 h-5 rounded hover:bg-red-100 hover:text-red-500 flex items-center justify-center text-[10px] flex-shrink-0 transition-opacity"
                          >
                            🗑
                          </button>
                        </>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
