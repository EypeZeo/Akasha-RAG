import { useState } from 'react';
import { useI18n } from '../i18n';

interface TraceStep {
  name: string;
  time_ms: number;
}

interface TraceChunk {
  chunk_id: string;
  title: string;
  score: number;
}

interface TraceData {
  route: string;
  steps: TraceStep[];
  chunks: TraceChunk[];
}

export default function ExecutionTrace({ trace, latency_ms }: { trace: TraceData; latency_ms?: number }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);

  if (!trace || !trace.steps) return null;

  const routeLabels: Record<string, string> = {
    direct: t('routeDirect'),
    db_list: t('routeDbList'),
    db_content: t('routeDbContent'),
    vector: t('routeVector'),
  };

  const hasChunks = trace.chunks && trace.chunks.length > 0;

  return (
    <div className="mt-2 border border-[var(--color-border)] rounded-xl overflow-hidden text-xs">
      {/* Header */}
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center gap-2 px-3 py-2 text-left hover:bg-black/[0.02] transition-colors"
      >
        <span className={`w-2 h-2 rounded-full ${open ? 'bg-accent' : 'bg-success'}`} />
        <span className="font-semibold text-[var(--color-ink-soft)]">
          {routeLabels[trace.route] || trace.route}
        </span>
        <span className="text-[var(--color-ink-muted)] font-mono">
          {trace.steps.map(s => `${s.time_ms}ms`).join(' + ')}
          {latency_ms ? ` = ${latency_ms}ms` : ''}
        </span>
        {hasChunks && (
          <span className="text-[var(--color-ink-muted)] ml-auto">
            {t('recallCount', { count: trace.chunks.length })}
          </span>
        )}
        <svg className={`w-3 h-3 text-[var(--color-ink-muted)] transition-transform ${open ? 'rotate-180' : ''}`} fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </button>

      {/* Body */}
      {open && (
        <div className="border-t border-dashed border-[var(--color-border)] px-3 py-2 space-y-2">
          {/* Steps */}
          <div className="space-y-1">
            {trace.steps.map((step, i) => (
              <div key={i} className="flex items-center gap-2">
                <span className="w-4 h-4 rounded-full bg-accent-light text-accent flex items-center justify-center font-mono text-[10px] font-bold">{i + 1}</span>
                <span className="flex-1 text-[var(--color-ink-soft)]">{step.name}</span>
                <span className="font-mono text-[var(--color-ink-muted)]">{step.time_ms}ms</span>
              </div>
            ))}
          </div>

          {/* Chunks */}
          {hasChunks && (
            <div className="pt-2 border-t border-dashed border-[var(--color-border)]">
              <p className="text-[10px] font-bold text-[var(--color-ink-muted)] uppercase tracking-wider mb-1.5">{t('recalledChunks')}</p>
              <div className="space-y-1.5">
                {trace.chunks.map((chunk, i) => (
                  <div key={i} className="p-2 rounded-lg bg-white/60 hover:bg-white/90 transition-colors cursor-default">
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-semibold text-[var(--color-ink)] truncate">{chunk.title}</span>
                      <span className="font-mono text-[var(--color-accent)] flex-shrink-0">{(chunk.score * 100).toFixed(0)}%</span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
