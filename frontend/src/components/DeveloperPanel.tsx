import { useState, useEffect, useRef, useCallback } from 'react';
import { useI18n } from '../i18n';
import { pollProgress } from '../utils/pollProgress';

// Metrics are diagnostic reads: they must never outlive a real user's patience,
// but they also must not be reported as a backend outage when the caller is the
// one that walked away. The in-flight guard prevents overlapping poll batches.
const METRIC_TIMEOUT_MS = 8_000;

async function readJson<T>(url: string, signal: AbortSignal, body?: string): Promise<T> {
  const headers: Record<string, string> = { 'X-Akasha-Client': '1' };
  if (body) {
    headers['Content-Type'] = 'application/json';
  }
  const response = await fetch(url, {
    signal,
    headers,
    ...(body ? { method: 'POST', body } : {}),
  });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}

/** True when the rejection is just this request's own AbortSignal firing. */
function isAbortError(reason: unknown): boolean {
  if (reason instanceof DOMException) return reason.name === 'AbortError';
  return reason instanceof Error && reason.name === 'AbortError';
}

interface SystemMetrics {
  process: {
    pid: number;
    cpu_percent: number;
    memory_mb: number;
    memory_percent: number;
    num_threads: number;
    uptime_seconds: number;
  };
  system: {
    platform: string;
    python_version: string;
    cpu_count: number;
    cpu_percent: number;
    memory_total_mb: number;
    memory_available_mb: number;
    memory_percent: number;
    disk_total_gb: number;
    disk_used_gb: number;
    disk_percent: number;
  };
}

interface NetworkMetrics {
  proxy: {
    detected: boolean;
    url: string | null;
    mode: string;
  };
  io_counters: {
    bytes_sent_mb: number;
    bytes_recv_mb: number;
    packets_sent: number;
    packets_recv: number;
    errin: number;
    errout: number;
    dropin: number;
    dropout: number;
  };
}

interface CacheMetrics {
  audio_cache: {
    size_mb: number;
    file_count: number;
    path: string;
    max_size_mb: number;
    retention_hours: number;
  };
  vector_db: {
    size_mb: number;
    path: string;
  };
  sqlite: {
    db_size_mb: number;
    wal_size_mb: number;
    total_size_mb: number;
  };
}

interface DatabaseMetrics {
  tables: {
    source_accounts: number;
    favorite_collections: number;
    content_items: number;
    collection_items: number;
    ingestion_items: number;
  };
}

export interface DeveloperPanelProps {
  active?: boolean;
}

interface MetricRequestBatch {
  controllers: AbortController[];
  cancelled: boolean;
}

export default function DeveloperPanel({ active = true }: DeveloperPanelProps = {}) {
  const { t, lang } = useI18n();
  const [developerMode, setDeveloperMode] = useState(false);
  const [systemMetrics, setSystemMetrics] = useState<SystemMetrics | null>(null);
  const [networkMetrics, setNetworkMetrics] = useState<NetworkMetrics | null>(null);
  const [cacheMetrics, setCacheMetrics] = useState<CacheMetrics | null>(null);
  const [databaseMetrics, setDatabaseMetrics] = useState<DatabaseMetrics | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const mountedRef = useRef(true);
  const toggleControllerRef = useRef<AbortController | null>(null);
  const inFlightRef = useRef<MetricRequestBatch | null>(null);

  // 检查开发者模式状态
  useEffect(() => {
    mountedRef.current = true;
    const controller = new AbortController();
    let disposed = false;
    const checkDeveloperMode = async () => {
      try {
        const data = await pollProgress(signal => readJson<{ enabled: boolean }>('/api/settings/developer-mode', signal), controller);
        if (disposed || controller.signal.aborted || !mountedRef.current) return;
        if (typeof data.enabled !== 'boolean') throw new Error('Invalid developer mode response');
        setDeveloperMode(data.enabled);
        setLoading(false);
      } catch (_err) {
        if (disposed || !mountedRef.current) return;
        setError(t('devBackendUnavailable'));
        setLoading(false);
      }
    };
    checkDeveloperMode();
    return () => {
      disposed = true;
      mountedRef.current = false;
      controller.abort();
      toggleControllerRef.current?.abort();
      toggleControllerRef.current = null;
    };
  }, [t]);

  const cancelInFlight = useCallback(() => {
    const batch = inFlightRef.current;
    if (!batch) return;
    batch.cancelled = true;
    batch.controllers.forEach(controller => controller.abort());
    inFlightRef.current = null;
  }, []);

  const fetchMetrics = useCallback(async (isManual = false) => {
    if (!mountedRef.current) return;
    if (inFlightRef.current) {
      if (isManual) {
        // A manual refresh supersedes only the batch that was already in flight.
        const previousBatch = inFlightRef.current;
        previousBatch.cancelled = true;
        previousBatch.controllers.forEach(controller => controller.abort());
        inFlightRef.current = null;
      } else {
        return;
      }
    }

    const controllers = Array.from({ length: 4 }, () => new AbortController());
    const batch: MetricRequestBatch = { controllers, cancelled: false };
    inFlightRef.current = batch;
    if (isManual) {
      setIsRefreshing(true);
    }

    const readMetric = <T,>(url: string, index: number) =>
      pollProgress(signal => readJson<T>(url, signal), controllers[index], METRIC_TIMEOUT_MS);

    let system: PromiseSettledResult<SystemMetrics>;
    let network: PromiseSettledResult<NetworkMetrics>;
    let cache: PromiseSettledResult<CacheMetrics>;
    let database: PromiseSettledResult<DatabaseMetrics>;
    try {
      [system, network, cache, database] = await Promise.allSettled([
        readMetric<SystemMetrics>('/api/system/diagnostics/system', 0),
        readMetric<NetworkMetrics>('/api/system/diagnostics/network', 1),
        readMetric<CacheMetrics>('/api/system/diagnostics/cache', 2),
        readMetric<DatabaseMetrics>('/api/system/diagnostics/database', 3),
      ]);
    } finally {
      // Only detach; never abort here.  Aborting a settled request in `finally`
      // is a no-op that still emits an AbortController.abort() call, which is
      // noise in DevTools for a request that already returned.
      if (inFlightRef.current === batch) {
        inFlightRef.current = null;
        if (mountedRef.current) setIsRefreshing(false);
      }
    }

    if (!mountedRef.current) return;
    if (batch.cancelled) {
      // This batch was explicitly superseded or abandoned; its results are stale.
      return;
    }

    const failed: string[] = [];
    const reasons: string[] = [];
    const deadlines: string[] = [];
    let anySuccess = false;

    const apply = <T,>(result: PromiseSettledResult<T>, update: (value: T) => void, label: string) => {
      if (result.status === 'fulfilled') {
        update(result.value);
        anySuccess = true;
        return;
      }
      failed.push(label);
      const reason = result.reason;
      // A per-request AbortError that this component did not request is the
      // request's own deadline firing (pollProgress aborts the controller).
      if (isAbortError(reason)) {
        deadlines.push(label);
        return;
      }
      const message = reason instanceof Error ? reason.message : String(reason || '');
      if (message) reasons.push(message);
    };

    apply(system, setSystemMetrics, t('devSystemResources'));
    apply(network, setNetworkMetrics, t('devNetworkStatus'));
    apply(cache, setCacheMetrics, t('devCacheStats'));
    apply(database, setDatabaseMetrics, t('devDatabaseStats'));

    if (anySuccess) {
      setLastUpdated(new Date());
    }

    let hint = '';
    if (deadlines.length > 0) {
      hint = t('devMetricsTimeout', { seconds: METRIC_TIMEOUT_MS / 1000 });
    } else if (reasons.length > 0 && reasons.every(r => r.includes('403'))) {
      hint = t('devMetricsModeDisabled');
    } else if (reasons.length > 0 && reasons.every(r => /failed to fetch|network|econnrefused|load failed/i.test(r))) {
      hint = t('devMetricsOffline');
    } else if (reasons.length > 0 && reasons.every(r => /^HTTP 499$/.test(r))) {
      hint = t('devMetricsIntercepted');
    }

    setError(failed.length
      ? t(failed.length === 4 ? 'devMetricsFailed' : 'devMetricsPartialFailed', { items: failed.join('、'), hint }) + t('devMetricsRetryNotice', { seconds: 5 })
      : '');
  }, [t]);

  // 仅在面板处于激活状态且开启开发者模式时轮询（每 5 秒）。
  // 面板在 Workspace 中始终保持挂载（用 hidden 切换可见性），所以非激活
  // 状态必须同时停掉轮询，否则后台标签页会一直打后端。
  useEffect(() => {
    if (!developerMode || !active) {
      cancelInFlight();
      return;
    }
    let disposed = false;

    fetchMetrics(false);
    const interval = setInterval(() => {
      if (!disposed) fetchMetrics(false);
    }, 5000);

    return () => {
      disposed = true;
      cancelInFlight();
      clearInterval(interval);
    };
  }, [developerMode, active, fetchMetrics, cancelInFlight]);

  const toggleDeveloperMode = async () => {
    if (toggleControllerRef.current) return;
    const controller = new AbortController();
    toggleControllerRef.current = controller;
    try {
      setError('');
      const data = await pollProgress(signal => readJson<{ enabled: boolean }>(
        '/api/settings/developer-mode', signal, JSON.stringify({ enabled: !developerMode }),
      ), controller);
      if (!mountedRef.current || controller.signal.aborted) return;
      if (typeof data.enabled !== 'boolean') throw new Error('Invalid developer mode response');
      setDeveloperMode(data.enabled);
    } catch (_err) {
      if (!mountedRef.current || toggleControllerRef.current !== controller) return;
      setError(t('devModeToggleFailed'));
    } finally {
      if (toggleControllerRef.current === controller) toggleControllerRef.current = null;
    }
  };

  const formatUptime = (seconds: number) => {
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const secs = Math.floor(seconds % 60);
    return t('devUptimeFormat', { hours, minutes, seconds: secs });
  };

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center">
        <div className="text-center">
          <div className="mb-2 h-8 w-8 animate-spin rounded-full border-2 border-gray-300 border-t-blue-500 mx-auto"></div>
          <p className="text-sm text-gray-500">{t('loadingVideos')}</p>
        </div>
      </div>
    );
  }

  if (!developerMode) {
    return (
      <div className="flex h-full items-center justify-center bg-gray-50 dark:bg-gray-900">
        <div className="max-w-md rounded-lg bg-white dark:bg-gray-800 p-8 text-center shadow-lg">
          <div className="mb-4 text-5xl">🔧</div>
          <h2 className="mb-2 text-xl font-semibold text-gray-900 dark:text-white">{t('devModeTitle')}</h2>
          <p className="mb-6 text-sm text-gray-600 dark:text-gray-400">
            {t('devModeDescription')}
          </p>
          <button
            onClick={toggleDeveloperMode}
            className="rounded-md bg-blue-500 px-6 py-2 text-sm font-medium text-white hover:bg-blue-600 transition-colors"
          >
            {t('devEnable')}
          </button>
          <p className="mt-4 text-xs text-gray-500">
            {t('devModePersistence')}
          </p>
          {error && <p role="alert" className="mt-4 text-sm text-red-600">{error}</p>}
        </div>
      </div>
    );
  }

  return (
    <div className="h-full overflow-y-auto bg-gray-50 dark:bg-gray-900 p-6">
      <div className="mx-auto max-w-7xl">
        {/* Header */}
        <div className="mb-6 flex flex-wrap items-center justify-between gap-4">
          <div>
            <h1 className="text-2xl font-bold text-gray-900 dark:text-white">{t('tabDeveloper')}</h1>
            <div className="mt-1 flex items-center gap-3 text-sm text-gray-600 dark:text-gray-400">
              <span>{t('devPanelSubtitle')}</span>
              {lastUpdated && (
                <span className="text-xs text-gray-400 dark:text-gray-500">
                  {t('devLastUpdated', { time: lastUpdated.toLocaleTimeString(lang) })}
                </span>
              )}
            </div>
          </div>
          <div className="flex items-center gap-3">
            <button
              onClick={() => fetchMetrics(true)}
              disabled={isRefreshing}
              title={t('devRefreshTitle')}
              className="inline-flex items-center gap-1.5 rounded-md border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-3 py-2 text-sm font-medium text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700 disabled:opacity-50 transition-colors shadow-xs"
            >
              <span className={`inline-block text-xs ${isRefreshing ? 'animate-spin' : ''}`}>🔄</span>
              <span>{isRefreshing ? t('devRefreshing') : t('devRefresh')}</span>
            </button>
            <button
              onClick={toggleDeveloperMode}
              className="rounded-md border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-4 py-2 text-sm font-medium text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors shadow-xs"
            >
              {t('devDisable')}
            </button>
          </div>
        </div>

        {error && (
          <div role="alert" className="mb-4 flex flex-col sm:flex-row sm:items-center justify-between gap-3 rounded-md bg-red-50 dark:bg-red-900/20 p-4 text-sm text-red-800 dark:text-red-400">
            <div className="flex items-start gap-2">
              <span className="font-bold shrink-0">⚠️</span>
              <span>{error}</span>
            </div>
            <button
              onClick={() => fetchMetrics(true)}
              disabled={isRefreshing}
              className="shrink-0 self-end sm:self-auto rounded bg-red-600 dark:bg-red-700 px-3 py-1.5 text-xs font-medium text-white hover:bg-red-700 dark:hover:bg-red-600 disabled:opacity-50 transition-colors shadow-xs"
            >
              {isRefreshing ? t('devRetrying') : t('devRetry')}
            </button>
          </div>
        )}

        <div className="grid gap-6 md:grid-cols-2">
          {/* 系统资源 */}
          <div className="rounded-lg bg-white dark:bg-gray-800 p-6 shadow">
            <div className="mb-4 flex items-center justify-between">
              <h3 className="text-lg font-semibold text-gray-900 dark:text-white">{t('devSystemResources')}</h3>
              {!systemMetrics && <span className="text-xs text-gray-400 dark:text-gray-500">{t('devNoData')}</span>}
            </div>
            <div className="space-y-3">
              <MetricRow label={t('devCpuUsage')} value={systemMetrics ? `${systemMetrics.system.cpu_percent}%` : '--'} />
              <MetricRow
                label={t('devMemoryUsage')}
                value={systemMetrics ? `${systemMetrics.system.memory_percent}%` : '--'}
                sublabel={systemMetrics ? `${(systemMetrics.system.memory_total_mb - systemMetrics.system.memory_available_mb).toFixed(0)} / ${systemMetrics.system.memory_total_mb.toFixed(0)} MB` : undefined}
              />
              <MetricRow
                label={t('devDiskUsage')}
                value={systemMetrics ? `${systemMetrics.system.disk_percent}%` : '--'}
                sublabel={systemMetrics ? `${systemMetrics.system.disk_used_gb.toFixed(1)} / ${systemMetrics.system.disk_total_gb.toFixed(1)} GB` : undefined}
              />
              <MetricRow label={t('devCpuCores')} value={systemMetrics ? String(systemMetrics.system.cpu_count) : '--'} />
              <MetricRow label={t('devOperatingSystem')} value={systemMetrics ? systemMetrics.system.platform : '--'} />
              <MetricRow label={t('devPythonVersion')} value={systemMetrics ? systemMetrics.system.python_version : '--'} />
            </div>
          </div>

          {/* 进程信息 */}
          <div className="rounded-lg bg-white dark:bg-gray-800 p-6 shadow">
            <div className="mb-4 flex items-center justify-between">
              <h3 className="text-lg font-semibold text-gray-900 dark:text-white">{t('devProcessInfo')}</h3>
              {!systemMetrics && <span className="text-xs text-gray-400 dark:text-gray-500">{t('devNoData')}</span>}
            </div>
            <div className="space-y-3">
              <MetricRow label={t('devProcessId')} value={systemMetrics ? String(systemMetrics.process.pid) : '--'} />
              <MetricRow label={t('devProcessCpu')} value={systemMetrics ? `${systemMetrics.process.cpu_percent}%` : '--'} />
              <MetricRow
                label={t('devProcessMemory')}
                value={systemMetrics ? `${systemMetrics.process.memory_mb.toFixed(1)} MB` : '--'}
                sublabel={systemMetrics ? t('devSystemMemory', { percent: systemMetrics.process.memory_percent.toFixed(2) }) : undefined}
              />
              <MetricRow label={t('devThreads')} value={systemMetrics ? String(systemMetrics.process.num_threads) : '--'} />
              <MetricRow label={t('devUptime')} value={systemMetrics ? formatUptime(systemMetrics.process.uptime_seconds) : '--'} />
            </div>
          </div>

          {/* 网络状态 */}
          <div className="rounded-lg bg-white dark:bg-gray-800 p-6 shadow">
            <div className="mb-4 flex items-center justify-between">
              <h3 className="text-lg font-semibold text-gray-900 dark:text-white">{t('devNetworkStatus')}</h3>
              {!networkMetrics && <span className="text-xs text-gray-400 dark:text-gray-500">{t('devNoData')}</span>}
            </div>
            <div className="space-y-3">
              <MetricRow
                label={t('devProxyMode')}
                value={networkMetrics ? networkMetrics.proxy.mode : '--'}
                sublabel={networkMetrics?.proxy.url || undefined}
              />
              <MetricRow
                label={t('devSentTraffic')}
                value={networkMetrics ? `${networkMetrics.io_counters.bytes_sent_mb.toFixed(2)} MB` : '--'}
                sublabel={networkMetrics ? t('devPackets', { count: networkMetrics.io_counters.packets_sent }) : undefined}
              />
              <MetricRow
                label={t('devReceivedTraffic')}
                value={networkMetrics ? `${networkMetrics.io_counters.bytes_recv_mb.toFixed(2)} MB` : '--'}
                sublabel={networkMetrics ? t('devPackets', { count: networkMetrics.io_counters.packets_recv }) : undefined}
              />
              <MetricRow
                label={t('devNetworkErrors')}
                value={networkMetrics ? t('devInboundOutbound', { inbound: networkMetrics.io_counters.errin, outbound: networkMetrics.io_counters.errout }) : '--'}
              />
              <MetricRow
                label={t('devPacketLoss')}
                value={networkMetrics ? t('devInboundOutbound', { inbound: networkMetrics.io_counters.dropin, outbound: networkMetrics.io_counters.dropout }) : '--'}
              />
            </div>
          </div>

          {/* 缓存统计 */}
          <div className="rounded-lg bg-white dark:bg-gray-800 p-6 shadow">
            <div className="mb-4 flex items-center justify-between">
              <h3 className="text-lg font-semibold text-gray-900 dark:text-white">{t('devCacheStats')}</h3>
              {!cacheMetrics && <span className="text-xs text-gray-400 dark:text-gray-500">{t('devNoData')}</span>}
            </div>
            <div className="space-y-3">
              <MetricRow
                label={t('devAudioCache')}
                value={cacheMetrics ? `${cacheMetrics.audio_cache.size_mb.toFixed(1)} MB` : '--'}
                sublabel={cacheMetrics ? t('devFileLimit', { count: cacheMetrics.audio_cache.file_count, limit: cacheMetrics.audio_cache.max_size_mb }) : undefined}
              />
              <MetricRow
                label={t('devVectorDatabase')}
                value={cacheMetrics ? `${cacheMetrics.vector_db.size_mb.toFixed(1)} MB` : '--'}
              />
              <MetricRow
                label={t('devSqliteDatabase')}
                value={cacheMetrics ? `${cacheMetrics.sqlite.db_size_mb.toFixed(1)} MB` : '--'}
                sublabel={cacheMetrics ? t('devWal', { size: cacheMetrics.sqlite.wal_size_mb.toFixed(1) }) : undefined}
              />
            </div>
          </div>

          {/* 数据库统计 */}
          <div className="rounded-lg bg-white dark:bg-gray-800 p-6 shadow md:col-span-2">
            <div className="mb-4 flex items-center justify-between">
              <h3 className="text-lg font-semibold text-gray-900 dark:text-white">{t('devDatabaseStats')}</h3>
              {!databaseMetrics && <span className="text-xs text-gray-400 dark:text-gray-500">{t('devNoData')}</span>}
            </div>
            <div className="grid gap-3 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-5">
              <MetricCard label={t('devAccounts')} value={databaseMetrics ? String(databaseMetrics.tables.source_accounts) : '--'} />
              <MetricCard label={t('devCollections')} value={databaseMetrics ? String(databaseMetrics.tables.favorite_collections) : '--'} />
              <MetricCard label={t('devContentItems')} value={databaseMetrics ? String(databaseMetrics.tables.content_items) : '--'} />
              <MetricCard label={t('devCollectionRelations')} value={databaseMetrics ? String(databaseMetrics.tables.collection_items) : '--'} />
              <MetricCard label={t('devIngestionItems')} value={databaseMetrics ? String(databaseMetrics.tables.ingestion_items) : '--'} />
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function MetricRow({ label, value, sublabel }: { label: string; value: string; sublabel?: string }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-sm text-gray-600 dark:text-gray-400">{label}</span>
      <div className="text-right">
        <span className="text-sm font-medium text-gray-900 dark:text-white">{value}</span>
        {sublabel && <div className="text-xs text-gray-500 dark:text-gray-500">{sublabel}</div>}
      </div>
    </div>
  );
}

function MetricCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md bg-gray-50 dark:bg-gray-700/50 p-4 text-center">
      <div className="text-2xl font-bold text-gray-900 dark:text-white">{value}</div>
      <div className="mt-1 text-xs text-gray-600 dark:text-gray-400">{label}</div>
    </div>
  );
}
