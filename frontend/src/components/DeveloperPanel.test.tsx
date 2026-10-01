import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import DeveloperPanel from './DeveloperPanel';
import { StrictMode } from 'react';

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('DeveloperPanel request boundaries', () => {
  it('does not report StrictMode effect cancellation as a backend failure', async () => {
    vi.stubGlobal('fetch', vi.fn().mockImplementation(async () => new Response('{"enabled":false}')));
    render(<StrictMode><DeveloperPanel /></StrictMode>);
    await screen.findByRole('button', { name: '启用开发者模式' });
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('rejects an HTTP failure without treating its JSON as metrics', async () => {
    const readErrorJson = vi.fn().mockResolvedValue({ detail: 'Not Found' });
    vi.stubGlobal('fetch', vi.fn()
      .mockResolvedValueOnce(new Response('{"enabled":true}'))
      .mockResolvedValue({ ok: false, status: 404, json: readErrorJson }));
    render(<DeveloperPanel />);
    expect(await screen.findByRole('alert')).toHaveTextContent('获取监控数据失败（系统资源、网络状态、缓存统计、数据库统计）');
    expect(readErrorJson).not.toHaveBeenCalled();
    expect(document.body.textContent).not.toContain('NaN');
  });

  it('does not overlap slow metric refreshes and aborts all requests on unmount', async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn().mockResolvedValueOnce(new Response('{"enabled":true}'))
      .mockImplementation(() => new Promise(() => {}));
    vi.stubGlobal('fetch', fetchMock);
    const { unmount } = render(<DeveloperPanel />);
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(fetchMock).toHaveBeenCalledTimes(5);
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(fetchMock).toHaveBeenCalledTimes(5);
    const signals = fetchMock.mock.calls.slice(1).map(call => call[1].signal as AbortSignal);
    unmount();
    expect(signals.every(signal => signal.aborted)).toBe(true);
  });

  it('shows a settings failure and permits only one concurrent mode toggle', async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce({ ok: false, status: 503 })
      .mockImplementation(() => new Promise(() => {}));
    vi.stubGlobal('fetch', fetchMock);
    const { unmount } = render(<DeveloperPanel />);
    expect(await screen.findByRole('alert')).toHaveTextContent('无法连接到后端服务');
    const enable = screen.getByRole('button', { name: '启用开发者模式' });
    fireEvent.click(enable);
    fireEvent.click(enable);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    const signal = fetchMock.mock.calls[1][1].signal as AbortSignal;
    unmount();
    expect(signal.aborted).toBe(true);
  });

  it('keeps successful cards visible when one metric fails and recovers on the next refresh', async () => {
    vi.useFakeTimers();
    let cacheFails = true;
    vi.stubGlobal('fetch', vi.fn().mockImplementation(async (url: string) => {
      if (url.endsWith('developer-mode')) return new Response('{"enabled":true}');
      if (url.endsWith('/cache') && cacheFails) return new Response('', { status: 500 });
      return new Response(JSON.stringify(metricData[url]));
    }));
    render(<DeveloperPanel />);
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(screen.getByRole('alert')).toHaveTextContent('获取部分监控数据失败（缓存统计）');
    expect(screen.getByRole('heading', { name: '系统资源' })).toBeTruthy();
    expect(screen.getByRole('heading', { name: '网络状态' })).toBeTruthy();
    expect(screen.getByRole('heading', { name: '数据库统计' })).toBeTruthy();
    cacheFails = false;
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(screen.queryByRole('alert')).toBeNull();
    expect(screen.getByRole('heading', { name: '缓存统计' })).toBeTruthy();
  });

  it('bounds a hung metric independently and ignores its late response after unmount', async () => {
    vi.useFakeTimers();
    let finishCache!: (response: Response) => void;
    vi.stubGlobal('fetch', vi.fn().mockImplementation(async (url: string) => {
      if (url.endsWith('developer-mode')) return new Response('{"enabled":true}');
      if (url.endsWith('/cache')) return new Promise<Response>(resolve => { finishCache = resolve; });
      return new Response(JSON.stringify(metricData[url]));
    }));
    const { unmount } = render(<DeveloperPanel />);
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    await act(async () => { await vi.advanceTimersByTimeAsync(15_000); });
    expect(screen.getByRole('alert')).toHaveTextContent('获取部分监控数据失败（缓存统计）');
    expect(screen.getByRole('heading', { name: '系统资源' })).toBeTruthy();
    unmount();
    await act(async () => { finishCache(new Response(JSON.stringify(metricData['/api/metrics/cache']))); });
    expect(screen.queryByRole('heading', { name: '缓存统计' })).toBeNull();
  });
});

const metricData: Record<string, unknown> = {
  '/api/metrics/system': {
    process: { pid: 1, cpu_percent: 1, memory_mb: 128, memory_percent: 1, num_threads: 4, uptime_seconds: 60 },
    system: { platform: 'Windows', python_version: '3.12', cpu_count: 4, cpu_percent: 2,
      memory_total_mb: 8192, memory_available_mb: 4096, memory_percent: 50,
      disk_total_gb: 100, disk_used_gb: 20, disk_percent: 20 },
  },
  '/api/metrics/network': {
    proxy: { detected: false, url: null, mode: 'direct/tun' },
    io_counters: { bytes_sent_mb: 1, bytes_recv_mb: 2, packets_sent: 3, packets_recv: 4,
      errin: 0, errout: 0, dropin: 0, dropout: 0 },
  },
  '/api/metrics/cache': {
    audio_cache: { size_mb: 1, file_count: 1, path: '', max_size_mb: 300, retention_hours: 24 },
    vector_db: { size_mb: 2, path: '' }, sqlite: { db_size_mb: 1, wal_size_mb: 0, total_size_mb: 1 },
  },
  '/api/metrics/database': {
    tables: { source_accounts: 3, favorite_collections: 10, content_items: 249, collection_items: 249, ingestion_items: 249 },
  },
};
