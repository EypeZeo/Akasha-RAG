import { useState, useEffect } from 'react';
import { useI18n } from '../i18n';

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

export default function DeveloperPanel() {
  const [developerMode, setDeveloperMode] = useState(false);
  const [systemMetrics, setSystemMetrics] = useState<SystemMetrics | null>(null);
  const [networkMetrics, setNetworkMetrics] = useState<NetworkMetrics | null>(null);
  const [cacheMetrics, setCacheMetrics] = useState<CacheMetrics | null>(null);
  const [databaseMetrics, setDatabaseMetrics] = useState<DatabaseMetrics | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  // 检查开发者模式状态
  useEffect(() => {
    const checkDeveloperMode = async () => {
      try {
        const res = await fetch('/api/settings/developer-mode', {
          headers: { 'X-Akasha-Client': '1' },
        });
        const data = await res.json();
        setDeveloperMode(data.enabled);
        setLoading(false);
      } catch (_err) {
        setError('无法连接到后端服务');
        setLoading(false);
      }
    };
    checkDeveloperMode();
  }, []);

  // 自动刷新指标（每 5 秒）
  useEffect(() => {
    if (!developerMode) return;

    const fetchMetrics = async () => {
      try {
        const [system, network, cache, database] = await Promise.all([
          fetch('/api/metrics/system', { headers: { 'X-Akasha-Client': '1' } }).then(r => r.json()),
          fetch('/api/metrics/network', { headers: { 'X-Akasha-Client': '1' } }).then(r => r.json()),
          fetch('/api/metrics/cache', { headers: { 'X-Akasha-Client': '1' } }).then(r => r.json()),
          fetch('/api/metrics/database', { headers: { 'X-Akasha-Client': '1' } }).then(r => r.json()),
        ]);
        setSystemMetrics(system);
        setNetworkMetrics(network);
        setCacheMetrics(cache);
        setDatabaseMetrics(database);
        setError('');
      } catch (_err) {
        setError('获取监控数据失败');
      }
    };

    fetchMetrics();
    const interval = setInterval(fetchMetrics, 5000);
    return () => clearInterval(interval);
  }, [developerMode]);

  const toggleDeveloperMode = async () => {
    try {
      const res = await fetch('/api/settings/developer-mode', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Akasha-Client': '1',
        },
        body: JSON.stringify({ enabled: !developerMode }),
      });
      const data = await res.json();
      setDeveloperMode(data.enabled);
    } catch (_err) {
      setError('切换开发者模式失败');
    }
  };

  const formatUptime = (seconds: number) => {
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const secs = Math.floor(seconds % 60);
    return `${hours}h ${minutes}m ${secs}s`;
  };

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center">
        <div className="text-center">
          <div className="mb-2 h-8 w-8 animate-spin rounded-full border-2 border-gray-300 border-t-blue-500 mx-auto"></div>
          <p className="text-sm text-gray-500">加载中...</p>
        </div>
      </div>
    );
  }

  if (!developerMode) {
    return (
      <div className="flex h-full items-center justify-center bg-gray-50 dark:bg-gray-900">
        <div className="max-w-md rounded-lg bg-white dark:bg-gray-800 p-8 text-center shadow-lg">
          <div className="mb-4 text-5xl">🔧</div>
          <h2 className="mb-2 text-xl font-semibold text-gray-900 dark:text-white">开发者模式</h2>
          <p className="mb-6 text-sm text-gray-600 dark:text-gray-400">
            启用开发者模式后可查看系统资源监控、网络状态、缓存统计等诊断信息
          </p>
          <button
            onClick={toggleDeveloperMode}
            className="rounded-md bg-blue-500 px-6 py-2 text-sm font-medium text-white hover:bg-blue-600 transition-colors"
          >
            启用开发者模式
          </button>
          <p className="mt-4 text-xs text-gray-500">
            注意：此设置不持久化，重启后需重新启用
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="h-full overflow-y-auto bg-gray-50 dark:bg-gray-900 p-6">
      <div className="mx-auto max-w-7xl">
        {/* Header */}
        <div className="mb-6 flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold text-gray-900 dark:text-white">开发者面板</h1>
            <p className="mt-1 text-sm text-gray-600 dark:text-gray-400">
              系统资源监控与诊断信息
            </p>
          </div>
          <button
            onClick={toggleDeveloperMode}
            className="rounded-md border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-4 py-2 text-sm text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors"
          >
            关闭开发者模式
          </button>
        </div>

        {error && (
          <div className="mb-4 rounded-md bg-red-50 dark:bg-red-900/20 p-4 text-sm text-red-800 dark:text-red-400">
            {error}
          </div>
        )}

        <div className="grid gap-6 md:grid-cols-2">
          {/* 系统资源 */}
          {systemMetrics && (
            <div className="rounded-lg bg-white dark:bg-gray-800 p-6 shadow">
              <h3 className="mb-4 text-lg font-semibold text-gray-900 dark:text-white">系统资源</h3>
              <div className="space-y-3">
                <MetricRow label="CPU 使用率" value={`${systemMetrics.system.cpu_percent}%`} />
                <MetricRow
                  label="内存使用"
                  value={`${systemMetrics.system.memory_percent}%`}
                  sublabel={`${(systemMetrics.system.memory_total_mb - systemMetrics.system.memory_available_mb).toFixed(0)} / ${systemMetrics.system.memory_total_mb.toFixed(0)} MB`}
                />
                <MetricRow
                  label="磁盘使用"
                  value={`${systemMetrics.system.disk_percent}%`}
                  sublabel={`${systemMetrics.system.disk_used_gb.toFixed(1)} / ${systemMetrics.system.disk_total_gb.toFixed(1)} GB`}
                />
                <MetricRow label="CPU 核心数" value={String(systemMetrics.system.cpu_count)} />
                <MetricRow label="Python 版本" value={systemMetrics.system.python_version} />
              </div>
            </div>
          )}

          {/* 进程信息 */}
          {systemMetrics && (
            <div className="rounded-lg bg-white dark:bg-gray-800 p-6 shadow">
              <h3 className="mb-4 text-lg font-semibold text-gray-900 dark:text-white">进程信息</h3>
              <div className="space-y-3">
                <MetricRow label="进程 PID" value={String(systemMetrics.process.pid)} />
                <MetricRow label="进程 CPU" value={`${systemMetrics.process.cpu_percent}%`} />
                <MetricRow
                  label="进程内存"
                  value={`${systemMetrics.process.memory_mb.toFixed(1)} MB`}
                  sublabel={`${systemMetrics.process.memory_percent.toFixed(2)}% 系统内存`}
                />
                <MetricRow label="线程数" value={String(systemMetrics.process.num_threads)} />
                <MetricRow label="运行时长" value={formatUptime(systemMetrics.process.uptime_seconds)} />
              </div>
            </div>
          )}

          {/* 网络状态 */}
          {networkMetrics && (
            <div className="rounded-lg bg-white dark:bg-gray-800 p-6 shadow">
              <h3 className="mb-4 text-lg font-semibold text-gray-900 dark:text-white">网络状态</h3>
              <div className="space-y-3">
                <MetricRow
                  label="代理模式"
                  value={networkMetrics.proxy.mode}
                  sublabel={networkMetrics.proxy.url || undefined}
                />
                <MetricRow
                  label="发送流量"
                  value={`${networkMetrics.io_counters.bytes_sent_mb.toFixed(2)} MB`}
                  sublabel={`${networkMetrics.io_counters.packets_sent} 数据包`}
                />
                <MetricRow
                  label="接收流量"
                  value={`${networkMetrics.io_counters.bytes_recv_mb.toFixed(2)} MB`}
                  sublabel={`${networkMetrics.io_counters.packets_recv} 数据包`}
                />
                <MetricRow
                  label="网络错误"
                  value={`入: ${networkMetrics.io_counters.errin} / 出: ${networkMetrics.io_counters.errout}`}
                />
                <MetricRow
                  label="丢包"
                  value={`入: ${networkMetrics.io_counters.dropin} / 出: ${networkMetrics.io_counters.dropout}`}
                />
              </div>
            </div>
          )}

          {/* 缓存统计 */}
          {cacheMetrics && (
            <div className="rounded-lg bg-white dark:bg-gray-800 p-6 shadow">
              <h3 className="mb-4 text-lg font-semibold text-gray-900 dark:text-white">缓存统计</h3>
              <div className="space-y-3">
                <MetricRow
                  label="音频缓存"
                  value={`${cacheMetrics.audio_cache.size_mb.toFixed(1)} MB`}
                  sublabel={`${cacheMetrics.audio_cache.file_count} 个文件 / 上限 ${cacheMetrics.audio_cache.max_size_mb} MB`}
                />
                <MetricRow
                  label="向量数据库"
                  value={`${cacheMetrics.vector_db.size_mb.toFixed(1)} MB`}
                />
                <MetricRow
                  label="SQLite 数据库"
                  value={`${cacheMetrics.sqlite.db_size_mb.toFixed(1)} MB`}
                  sublabel={`WAL: ${cacheMetrics.sqlite.wal_size_mb.toFixed(1)} MB`}
                />
              </div>
            </div>
          )}

          {/* 数据库统计 */}
          {databaseMetrics && (
            <div className="rounded-lg bg-white dark:bg-gray-800 p-6 shadow md:col-span-2">
              <h3 className="mb-4 text-lg font-semibold text-gray-900 dark:text-white">数据库统计</h3>
              <div className="grid gap-3 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-5">
                <MetricCard label="账号" value={String(databaseMetrics.tables.source_accounts)} />
                <MetricCard label="收藏夹" value={String(databaseMetrics.tables.favorite_collections)} />
                <MetricCard label="内容项" value={String(databaseMetrics.tables.content_items)} />
                <MetricCard label="收藏关系" value={String(databaseMetrics.tables.collection_items)} />
                <MetricCard label="入库项" value={String(databaseMetrics.tables.ingestion_items)} />
              </div>
            </div>
          )}
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
