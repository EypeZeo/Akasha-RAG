import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import DeveloperPanel from './DeveloperPanel';
import { StrictMode } from 'react';
import { I18nProvider } from '../i18n';

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  localStorage.removeItem('akasha:lang');
});

describe('DeveloperPanel request boundaries', () => {
  it('renders its labels in the selected interface language', async () => {
    localStorage.setItem('akasha:lang', 'en');
    vi.stubGlobal('fetch', vi.fn().mockImplementation(async (url: string) => {
      if (url.endsWith('developer-mode')) return new Response('{"enabled":true}');
      return new Response(JSON.stringify(metricData[url]));
    }));
    render(<I18nProvider><DeveloperPanel /></I18nProvider>);
    expect(await screen.findByRole('heading', { name: 'System resources' })).toBeTruthy();
    expect(screen.getByRole('button', { name: /Refresh data/ })).toBeTruthy();
    expect(screen.getByText('Network status')).toBeTruthy();
    expect(screen.queryByText('系统资源')).toBeNull();
  });

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

    const metricSignals = () =>
      fetchMock.mock.calls.slice(1).map(call => call[1].signal as AbortSignal);
    const batchOne = metricSignals();

    // Still inside the per-request deadline: the 5s poll must not stack a
    // second batch on top of a batch that has not produced anything yet.
    await act(async () => { await vi.advanceTimersByTimeAsync(4_000); });
    expect(metricSignals()).toHaveLength(4);

    // Past the deadline the hung batch gives up, which frees the next poll.
    await act(async () => { await vi.advanceTimersByTimeAsync(6_000); });
    expect(batchOne.every(signal => signal.aborted)).toBe(true);
    expect(metricSignals().length).toBeGreaterThan(4);

    const allSignals = metricSignals();
    unmount();
    expect(allSignals.every(signal => signal.aborted)).toBe(true);
  });

  it('reports a per-request deadline as a timeout, not as a backend outage', async () => {
    vi.useFakeTimers();
    vi.stubGlobal('fetch', vi.fn().mockImplementation(async (url: string) => {
      if (url.endsWith('developer-mode')) return new Response('{"enabled":true}');
      if (url.endsWith('/cache')) return new Promise<Response>(() => {});
      return new Response(JSON.stringify(metricData[url]));
    }));
    render(<DeveloperPanel />);
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    const alert = screen.getByRole('alert');
    expect(alert).toHaveTextContent('获取部分监控数据失败（缓存统计）');
    expect(alert).toHaveTextContent('请求超过 8 秒未返回，已取消');
    expect(alert).not.toHaveTextContent('后端服务未连接或已关闭');
    expect(screen.getByRole('heading', { name: '系统资源' })).toBeTruthy();
  });

  it('does not advance the last-updated stamp for a batch that produced nothing', async () => {
    // Deliberately real timers: this test drives the request deadline through a
    // setTimeout spy rather than useFakeTimers, because the neighbouring tests
    // install and tear down fake timers and the interaction made this scenario
    // silently hang (the batch never settled, so nothing was asserted at all).
    const realSetTimeout = globalThis.setTimeout;
    const timeoutSpy = vi.spyOn(globalThis, 'setTimeout').mockImplementation(
      ((handler: TimerHandler, delay?: number, ...rest: unknown[]) => {
        if (delay === 8_000) {
          // This is pollProgress's request deadline: fire it straight away.
          if (typeof handler === 'function') (handler as (...a: unknown[]) => void)(...rest);
          return 0 as unknown as ReturnType<typeof setTimeout>;
        }
        return realSetTimeout(handler as never, delay, ...(rest as never[]));
      }) as typeof setTimeout,
    );
    try {
      vi.stubGlobal('fetch', vi.fn().mockImplementation((url: string, init?: RequestInit) => {
        if (url.endsWith('developer-mode')) return Promise.resolve(new Response('{"enabled":true}'));
        // One metric fails immediately. It matters: the controller that is not
        // aborted is what stops the whole batch from reading as a self-inflicted
        // cancel, which the component deliberately suppresses without an alert.
        if (url.endsWith('/cache')) return Promise.resolve(new Response('', { status: 500 }));
        // The rest never answer; they settle only when their deadline fires.
        return new Promise<Response>((_resolve, reject) => {
          init?.signal?.addEventListener('abort', () => {
            reject(new DOMException('The operation was aborted.', 'AbortError'));
          }, { once: true });
        });
      }));
      render(<DeveloperPanel />);
      await act(async () => {});
      const alert = screen.getByRole('alert');
      expect(alert).toHaveTextContent('获取监控数据失败（系统资源、网络状态、缓存统计、数据库统计）');
      expect(alert).toHaveTextContent('请求超过 8 秒未返回，已取消');
      // Nothing succeeded, so no batch may be reported as fresh data.
      expect(screen.queryByText(/上次更新/)).toBeNull();
      expect(timeoutSpy).toHaveBeenCalled();
    } finally {
      timeoutSpy.mockRestore();
    }
  });

  it('preempts an in-flight batch on manual refresh without reporting a failure', async () => {
    vi.useFakeTimers();
    let abortedAsFetch: number | null = null;
    const fetchMock = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      if (url.endsWith('developer-mode')) return Promise.resolve(new Response('{"enabled":true}'));
      if (abortedAsFetch === null) {
        // First batch: hang the way a slow backend does, but stay responsive to
        // AbortSignal — a mock that ignores its signal never settles, which
        // silently skips the entire reporting path being tested here.
        return new Promise<Response>((_resolve, reject) => {
          init?.signal?.addEventListener('abort', () => {
            abortedAsFetch = fetchMock.mock.calls.length;
            reject(new DOMException('The operation was aborted.', 'AbortError'));
          }, { once: true });
        });
      }
      return Promise.resolve(new Response(JSON.stringify(metricData[url])));
    });
    vi.stubGlobal('fetch', fetchMock);
    render(<DeveloperPanel />);
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });

    const batchOne = fetchMock.mock.calls
      .slice(1, 5)
      .map(call => call[1]?.signal as AbortSignal);
    expect(batchOne).toHaveLength(4);
    expect(batchOne.some(signal => signal.aborted)).toBe(false);

    fireEvent.click(screen.getByRole('button', { name: /刷新数据/ }));
    // Locks the user-visible guarantee: a refresh while a batch is genuinely in
    // flight preempts it and still ends with fresh data, never a failure banner.
    // The classification itself is pinned by the tab-switch and timeout tests.
    expect(batchOne.every(signal => signal.aborted)).toBe(true);
    expect(abortedAsFetch).not.toBeNull();
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });

    expect(screen.queryByRole('alert')).toBeNull();
    expect(screen.getByRole('heading', { name: '系统资源' })).toBeTruthy();
  });

  it('never aborts metric requests that already completed', async () => {
    vi.useFakeTimers();
    // Count aborts on the metric requests only; the fetch metrics controllers are
    // fresh per batch, so any abort here means the component cancelled a request
    // that had already returned.
    let metricAborts = 0;
    vi.stubGlobal('fetch', vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      if (url.endsWith('developer-mode')) return Promise.resolve(new Response('{"enabled":true}'));
      init?.signal?.addEventListener('abort', () => { metricAborts += 1; }, { once: true });
      return Promise.resolve(new Response(JSON.stringify(metricData[url])));
    }));

    const { unmount } = render(<DeveloperPanel active />);
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    // Several poll cycles, all answering immediately: nothing is ever in flight
    // when a batch finishes, so nothing may be aborted.
    await act(async () => { await vi.advanceTimersByTimeAsync(20_000); });
    unmount();
    await act(async () => { await vi.advanceTimersByTimeAsync(100); });
    expect(metricAborts).toBe(0);
  });

  it('keeps a replacement refresh busy while the cancelled batch finishes', async () => {
    vi.useFakeTimers();
    const pending: Array<() => void> = [];
    vi.stubGlobal('fetch', vi.fn().mockImplementation((url: string) => {
      if (url.endsWith('developer-mode')) return Promise.resolve(new Response('{"enabled":true}'));
      return new Promise<Response>(resolve => {
        pending.push(() => resolve(new Response(JSON.stringify(metricData[url]))));
      });
    }));
    render(<DeveloperPanel />);
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    fireEvent.click(screen.getByRole('button', { name: /刷新数据/ }));
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(screen.getByRole('button', { name: /刷新中/ })).toBeDisabled();
    expect(pending).toHaveLength(8);
    await act(async () => { pending.slice(4).forEach(finish => finish()); });
    expect(screen.getByRole('button', { name: /刷新数据/ })).toBeEnabled();
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('suppresses the abandoned batch when the panel goes inactive', async () => {
    vi.useFakeTimers();
    const signals: AbortSignal[] = [];
    vi.stubGlobal('fetch', vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      if (url.endsWith('developer-mode')) return Promise.resolve(new Response('{"enabled":true}'));
      if (init?.signal) signals.push(init.signal);
      // Answers only once its own deadline fires, so it is still in flight when
      // the panel is switched away.
      return new Promise<Response>((_resolve, reject) => {
        init?.signal?.addEventListener('abort', () => {
          reject(new DOMException('The operation was aborted.', 'AbortError'));
        }, { once: true });
      });
    }));
    const { rerender } = render(<DeveloperPanel active />);
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(signals).toHaveLength(4);

    rerender(<DeveloperPanel active={false} />);
    expect(signals.every(signal => signal.aborted)).toBe(true);
    await act(async () => { await vi.advanceTimersByTimeAsync(10); });

    // The abandoned batch must be silent: no outage banner for our own cancel.
    expect(screen.queryByRole('alert')).toBeNull();
    expect(screen.queryByText(/上次更新/)).toBeNull();
  });

  it('stops polling metrics while the panel is not the active tab', async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn().mockImplementation(async (url: string) => {
      if (url.endsWith('developer-mode')) return new Response('{"enabled":true}');
      return new Response(JSON.stringify(metricData[url]));
    });
    vi.stubGlobal('fetch', fetchMock);
    const metricCalls = () => fetchMock.mock.calls.filter(call => String(call[0]).includes('/api/system/diagnostics/')).length;
    const { rerender } = render(<DeveloperPanel active />);
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(metricCalls()).toBe(4);

    rerender(<DeveloperPanel active={false} />);
    await act(async () => { await vi.advanceTimersByTimeAsync(20_000); });
    expect(metricCalls()).toBe(4);

    rerender(<DeveloperPanel active />);
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(metricCalls()).toBe(8);
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
    await act(async () => { finishCache(new Response(JSON.stringify(metricData['/api/system/diagnostics/cache']))); });
    expect(screen.queryByRole('heading', { name: '缓存统计' })).toBeNull();
  });

  it('displays a dedicated hint when the backend service is offline', async () => {
    vi.stubGlobal('fetch', vi.fn()
      .mockResolvedValueOnce(new Response('{"enabled":true}'))
      .mockRejectedValue(new TypeError('Failed to fetch')));
    render(<DeveloperPanel />);
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('获取监控数据失败（系统资源、网络状态、缓存统计、数据库统计）：后端服务未连接或已关闭');
  });

  it('uses local diagnostics paths and does not mistake HTTP 499 for caller cancellation', async () => {
    const fetchMock = vi.fn().mockImplementation(async (url: string) => {
      if (url.endsWith('developer-mode')) return new Response('{"enabled":true}');
      return new Response('', { status: 499, headers: { 'Content-Type': 'image/png' } });
    });
    vi.stubGlobal('fetch', fetchMock);
    render(<DeveloperPanel />);
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('HTTP 499');
    expect(alert).not.toHaveTextContent('浏览器取消');
    expect(fetchMock.mock.calls.map(call => call[0]).slice(1)).toEqual([
      '/api/system/diagnostics/system', '/api/system/diagnostics/network',
      '/api/system/diagnostics/cache', '/api/system/diagnostics/database',
    ]);
  });

  it('displays a dedicated hint when the backend developer mode is disabled or reset', async () => {
    vi.stubGlobal('fetch', vi.fn()
      .mockResolvedValueOnce(new Response('{"enabled":true}'))
      .mockResolvedValue({ ok: false, status: 403 }));
    render(<DeveloperPanel />);
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('获取监控数据失败（系统资源、网络状态、缓存统计、数据库统计）：后端开发者模式未开启或已重置');
  });

  it('renders structured placeholder cards with -- instead of collapsing when metrics fail', async () => {
    vi.stubGlobal('fetch', vi.fn()
      .mockResolvedValueOnce(new Response('{"enabled":true}'))
      .mockRejectedValue(new Error('Network error')));
    render(<DeveloperPanel />);
    await screen.findByRole('alert');
    expect(screen.getByRole('heading', { name: '系统资源' })).toBeTruthy();
    expect(screen.getByRole('heading', { name: '进程信息' })).toBeTruthy();
    expect(screen.getByRole('heading', { name: '网络状态' })).toBeTruthy();
    expect(screen.getByRole('heading', { name: '缓存统计' })).toBeTruthy();
    expect(screen.getByRole('heading', { name: '数据库统计' })).toBeTruthy();
    expect(screen.getAllByText('--').length).toBeGreaterThan(10);
  });

  it('allows manual refresh via header button and retry button in the error alert', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response('{"enabled":true}'))
      .mockRejectedValueOnce(new Error('fail 1'))
      .mockRejectedValueOnce(new Error('fail 2'))
      .mockRejectedValueOnce(new Error('fail 3'))
      .mockRejectedValueOnce(new Error('fail 4'))
      .mockImplementation(async (url: string) => new Response(JSON.stringify(metricData[url])));
    vi.stubGlobal('fetch', fetchMock);

    render(<DeveloperPanel />);
    const alert = await screen.findByRole('alert');
    expect(alert).toBeInTheDocument();

    const retryBtn = screen.getByRole('button', { name: '立即重试' });
    fireEvent.click(retryBtn);

    // After retry succeeds, alert is dismissed and real data is shown
    await act(async () => {});
    expect(screen.queryByRole('alert')).toBeNull();
    expect(screen.getByText('Windows')).toBeInTheDocument();

    // Now test header refresh button
    const refreshBtn = screen.getByRole('button', { name: /刷新数据/ });
    fireEvent.click(refreshBtn);
    await act(async () => {});
    expect(fetchMock.mock.calls.length).toBeGreaterThanOrEqual(9);
  });

  it('does not send Content-Type header on GET requests', async () => {
    const fetchMock = vi.fn().mockImplementation(async (url: string) => {
      if (url.endsWith('developer-mode')) return new Response('{"enabled":true}');
      return new Response(JSON.stringify(metricData[url]));
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<DeveloperPanel />);
    await screen.findByRole('heading', { name: '系统资源' });

    const getCalls = fetchMock.mock.calls.filter(call => !call[1]?.method || call[1].method === 'GET');
    expect(getCalls.length).toBeGreaterThanOrEqual(4);
    for (const call of getCalls) {
      expect(call[1]?.headers).toBeDefined();
      expect(call[1]?.headers['Content-Type']).toBeUndefined();
      expect(call[1]?.headers['X-Akasha-Client']).toBe('1');
    }
  });
});

const metricData: Record<string, unknown> = {
  '/api/system/diagnostics/system': {
    process: { pid: 1, cpu_percent: 1, memory_mb: 128, memory_percent: 1, num_threads: 4, uptime_seconds: 60 },
    system: { platform: 'Windows', python_version: '3.12', cpu_count: 4, cpu_percent: 2,
      memory_total_mb: 8192, memory_available_mb: 4096, memory_percent: 50,
      disk_total_gb: 100, disk_used_gb: 20, disk_percent: 20 },
  },
  '/api/system/diagnostics/network': {
    proxy: { detected: false, url: null, mode: 'direct/tun' },
    io_counters: { bytes_sent_mb: 1, bytes_recv_mb: 2, packets_sent: 3, packets_recv: 4,
      errin: 0, errout: 0, dropin: 0, dropout: 0 },
  },
  '/api/system/diagnostics/cache': {
    audio_cache: { size_mb: 1, file_count: 1, path: '', max_size_mb: 300, retention_hours: 24 },
    vector_db: { size_mb: 2, path: '' }, sqlite: { db_size_mb: 1, wal_size_mb: 0, total_size_mb: 1 },
  },
  '/api/system/diagnostics/database': {
    tables: { source_accounts: 3, favorite_collections: 10, content_items: 249, collection_items: 249, ingestion_items: 249 },
  },
};
