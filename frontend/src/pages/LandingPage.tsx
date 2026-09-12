import { useI18n } from '../i18n';

interface Props { onStartLogin: () => void; busy: boolean; }

export default function LandingPage({ onStartLogin, busy }: Props) {
  const { t } = useI18n();

  return (
    <div className="min-h-screen flex flex-col bg-[var(--color-bg)]">
      {/* Top Bar */}
      <header className="flex items-center justify-between px-8 py-4 bg-white/50 backdrop-blur border-b border-black/[0.04]">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl flex items-center justify-center p-1 bg-white/70 border border-black/5 shadow-sm overflow-hidden">
            <img src="/akasha-mark.svg" alt="Akasha-RAG" className="w-full h-full object-contain" />
          </div>
          <span className="font-display text-lg font-bold text-[var(--color-ink)]">{t('appTitle')}</span>
        </div>
        <button
          onClick={onStartLogin}
          disabled={busy}
          className="px-5 py-2 rounded-full bg-accent hover:bg-accent-hover text-white text-xs font-semibold shadow-sm transition-all hover:shadow-md disabled:opacity-60 disabled:cursor-wait cursor-pointer"
        >
          {busy ? t('startingLogin') : t('startLogin')}
        </button>
      </header>

      {/* Hero */}
      <main className="flex-1 flex flex-col items-center justify-center text-center px-6 gap-10">
        <span className="px-5 py-2 rounded-full bg-accent-light text-accent text-xs tracking-[0.15em] uppercase">
          {t('landingBadge')}
        </span>

        <h1 className="font-display text-5xl leading-tight text-[var(--color-ink)] max-w-lg whitespace-pre-line">
          {t('landingHeroTitle')}
        </h1>

        <p className="text-[var(--color-ink-soft)] text-base max-w-md leading-relaxed">
          {t('landingHeroDesc')}
        </p>

        <button
          onClick={onStartLogin}
          disabled={busy}
          className="px-10 py-4 rounded-full bg-gradient-to-r from-accent to-accent-hover text-white font-bold text-base
                     shadow-lg shadow-accent/25 hover:shadow-xl hover:shadow-accent/30
                     transition-all hover:-translate-y-0.5 disabled:opacity-60 disabled:cursor-wait"
        >
          {busy ? t('startingLogin') : t('landingStartBtn')}
        </button>

        {/* Pipeline */}
        <div className="flex gap-10 mt-6">
          {[
            ['1', t('stepSync'), t('stepSyncDesc')],
            ['2', t('stepExtract'), t('stepExtractDesc')],
            ['3', t('stepSearch'), t('stepSearchDesc')],
            ['4', t('stepChat'), t('stepChatDesc')],
          ].map(([n, label, desc]) => (
            <div key={n} className="flex flex-col items-center gap-2 w-20">
              <div className="w-9 h-9 rounded-full bg-accent-light flex items-center justify-center text-accent font-bold text-sm">{n}</div>
              <span className="text-sm font-semibold text-[var(--color-ink)]">{label}</span>
              <span className="text-xs text-[var(--color-ink-muted)]">{desc}</span>
            </div>
          ))}
        </div>
      </main>

      {/* Footer */}
      <footer className="py-3 text-center text-xs text-[var(--color-ink-muted)] bg-white/30">
        {t('footerText')}
      </footer>
    </div>
  );
}
