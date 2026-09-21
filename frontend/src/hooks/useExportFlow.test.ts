import { StrictMode } from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, waitFor, act } from '@testing-library/react';
import { useExportFlow } from './useExportFlow';
import * as api from '../api';

vi.mock('../api');

const ACTIVE_EXPORT_KEY = 'akasha:active_export';

const t = (key: string) => key;
const deferred = <T,>() => {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(done => { resolve = done; });
  return { promise, resolve };
};

/** Replace anchor clicks (the browser download) with a spy. */
function spyOnDownloads() {
  const clickSpy = vi.fn();
  const originalCreateElement = document.createElement.bind(document);
  vi.spyOn(document, 'createElement').mockImplementation((tag: string) => {
    const element = originalCreateElement(tag);
    if (tag === 'a') (element as HTMLAnchorElement).click = clickSpy;
    return element;
  });
  return clickSpy;
}

beforeEach(() => {
  localStorage.clear();
  vi.mocked(api.exportDownloadUrl).mockImplementation(id => `https://example.test/download/${id}`);
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe('useExportFlow', () => {
  it('starts a task as queued and picks up status changes from polling', async () => {
    vi.mocked(api.getExportProgress).mockResolvedValue({
      success: true, status: 'running', progress: 2, total: 5,
    } as any);

    const { result } = renderHook(() => useExportFlow(t));

    act(() => {
      result.current.startExportPolling('task-1', 'local');
    });
    expect(result.current.exportTask?.status).toBe('queued');

    await waitFor(() => {
      expect(result.current.exportTask?.status).toBe('running');
    });
    expect(result.current.exportTask?.progress).toBe(2);
    expect(result.current.exportTask?.total).toBe(5);
  });

  it('stops polling once dismissExportCard is called', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.mocked(api.getExportProgress).mockResolvedValue({ success: true, status: 'running', progress: 1, total: 3 } as any);

    const { result } = renderHook(() => useExportFlow(t));

    await act(async () => {
      result.current.startExportPolling('task-2', 'local');
      await vi.advanceTimersByTimeAsync(0);
    });
    const callsAfterStart = vi.mocked(api.getExportProgress).mock.calls.length;
    expect(callsAfterStart).toBeGreaterThan(0);

    act(() => {
      result.current.dismissExportCard();
    });
    expect(result.current.exportTask).toBeNull();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    // No new polling calls after dismissal — the interval was really cleared,
    // not just visually hidden behind exportTask === null.
    expect(vi.mocked(api.getExportProgress).mock.calls.length).toBe(callsAfterStart);
  });

  it('resumes polling for a task left in localStorage from a previous page load', async () => {
    localStorage.setItem('akasha:active_export', JSON.stringify({ id: 'task-3', mode: 'browser' }));
    vi.mocked(api.getExportProgress).mockResolvedValue({ success: true, status: 'running', progress: 4, total: 10 } as any);

    const { result } = renderHook(() => useExportFlow(t));

    await waitFor(() => {
      expect(result.current.exportTask?.id).toBe('task-3');
    });
    expect(result.current.exportTask?.mode).toBe('browser');
    expect(api.getExportProgress).toHaveBeenCalledWith('task-3');
  });

  it('only triggers one real download per task id even if called repeatedly', () => {
    const { result } = renderHook(() => useExportFlow(t));
    const clickSpy = vi.fn();
    const originalCreateElement = document.createElement.bind(document);
    const createElementSpy = vi.spyOn(document, 'createElement').mockImplementation((tag: string) => {
      const el = originalCreateElement(tag);
      if (tag === 'a') (el as HTMLAnchorElement).click = clickSpy;
      return el;
    });

    act(() => {
      result.current.triggerBrowserDownload('task-4');
      result.current.triggerBrowserDownload('task-4');
    });

    expect(clickSpy).toHaveBeenCalledTimes(1);
    createElementSpy.mockRestore();
  });

  it('does not starve a slow poll by starting overlapping interval requests', async () => {
    vi.useFakeTimers();
    const pending = deferred<api.ExportProgress>();
    vi.mocked(api.getExportProgress).mockReturnValue(pending.promise);
    const { result } = renderHook(() => useExportFlow(t));

    act(() => result.current.startExportPolling('slow-task', 'local'));
    expect(api.getExportProgress).toHaveBeenCalledTimes(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(6_000); });
    expect(api.getExportProgress).toHaveBeenCalledTimes(1);

    await act(async () => {
      pending.resolve({ success: true, status: 'running', progress: 3, total: 9 });
      await Promise.resolve();
    });
    expect(result.current.exportTask?.progress).toBe(3);
    await act(async () => { await vi.advanceTimersByTimeAsync(1_500); });
    expect(api.getExportProgress).toHaveBeenCalledTimes(2);
  });

  it('restores a completed browser task without auto-downloading and keeps manual download usable', async () => {
    localStorage.setItem(ACTIVE_EXPORT_KEY, JSON.stringify({ id: 'restored', mode: 'browser' }));
    vi.mocked(api.getExportProgress).mockResolvedValue({ success: true, status: 'done', progress: 1, total: 1 });
    const clickSpy = spyOnDownloads();

    const { result } = renderHook(() => useExportFlow(t));
    await waitFor(() => expect(result.current.exportTask?.status).toBe('done'));
    expect(clickSpy).not.toHaveBeenCalled();

    act(() => {
      result.current.triggerBrowserDownload('restored');
      result.current.triggerBrowserDownload('restored');
    });
    expect(clickSpy).toHaveBeenCalledTimes(1);
  });

  it('a response that outlives its task cannot overwrite the next task, download, or stop its polling', async () => {
    vi.useFakeTimers();
    const stale = deferred<api.ExportProgress>();
    vi.mocked(api.getExportProgress).mockImplementation(async (taskId: string) => (
      taskId === 'task-a' ? stale.promise : { success: true, status: 'running', progress: 1, total: 4 }
    ));
    const clickSpy = spyOnDownloads();
    const { result } = renderHook(() => useExportFlow(t));

    act(() => result.current.startExportPolling('task-a', 'browser')); // A's first poll stays pending
    act(() => result.current.dismissExportCard());
    await act(async () => {
      result.current.startExportPolling('task-b', 'local');
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(result.current.exportTask).toMatchObject({ id: 'task-b', status: 'running', progress: 1 });

    await act(async () => {
      stale.resolve({ success: true, status: 'done', progress: 9, total: 9 });
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(result.current.exportTask).toMatchObject({ id: 'task-b', status: 'running', progress: 1 });
    expect(clickSpy).not.toHaveBeenCalled();

    // A's stale terminal response must not have cleared B's interval.
    vi.mocked(api.getExportProgress).mockClear();
    await act(async () => { await vi.advanceTimersByTimeAsync(1_500); });
    expect(api.getExportProgress).toHaveBeenCalledWith('task-b');
  });

  it('a task started while another is still being polled is not overwritten by the old one\'s late response', async () => {
    vi.useFakeTimers();
    const stale = deferred<api.ExportProgress>();
    vi.mocked(api.getExportProgress).mockImplementation(async (taskId: string) => (
      taskId === 'task-a' ? stale.promise : { success: true, status: 'running', progress: 1, total: 4 }
    ));
    const { result } = renderHook(() => useExportFlow(t));

    act(() => result.current.startExportPolling('task-a', 'local'));
    await act(async () => {
      result.current.startExportPolling('task-b', 'local');
      await vi.advanceTimersByTimeAsync(0);
    });
    await act(async () => {
      stale.resolve({ success: true, status: 'done', progress: 9, total: 9 });
      await vi.advanceTimersByTimeAsync(0);
    });

    expect(result.current.exportTask).toMatchObject({ id: 'task-b', status: 'running' });
  });

  it('a restored browser task that only finishes on a later poll is not auto-downloaded either', async () => {
    vi.useFakeTimers();
    localStorage.setItem(ACTIVE_EXPORT_KEY, JSON.stringify({ id: 'restored-late', mode: 'browser' }));
    let status: 'running' | 'done' = 'running';
    vi.mocked(api.getExportProgress).mockImplementation(async () => ({ success: true, status, progress: 1, total: 1 }));
    const clickSpy = spyOnDownloads();
    const { result } = renderHook(() => useExportFlow(t));
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(result.current.exportTask?.status).toBe('running');

    status = 'done';
    await act(async () => { await vi.advanceTimersByTimeAsync(1_500); });

    expect(result.current.exportTask?.status).toBe('done');
    expect(clickSpy).not.toHaveBeenCalled();
  });

  it('a response that arrives after unmount does not download and leaves the task remembered for the next mount', async () => {
    vi.useFakeTimers();
    const pending = deferred<api.ExportProgress>();
    vi.mocked(api.getExportProgress).mockReturnValue(pending.promise);
    const clickSpy = spyOnDownloads();
    const { result, unmount } = renderHook(() => useExportFlow(t));

    act(() => result.current.startExportPolling('task-u', 'browser'));
    unmount();
    await act(async () => {
      pending.resolve({ success: true, status: 'done', progress: 1, total: 1 });
      await vi.advanceTimersByTimeAsync(0);
    });

    expect(clickSpy).not.toHaveBeenCalled();
    // Still remembered, so the next mount can offer the finished task's download button.
    expect(JSON.parse(localStorage.getItem(ACTIVE_EXPORT_KEY)!)).toEqual({ id: 'task-u', mode: 'browser' });
  });

  it('unmounting stops the poll', async () => {
    vi.useFakeTimers();
    vi.mocked(api.getExportProgress).mockResolvedValue({ success: true, status: 'running', progress: 1, total: 3 });
    const { result, unmount } = renderHook(() => useExportFlow(t));
    await act(async () => {
      result.current.startExportPolling('task-x', 'local');
      await vi.advanceTimersByTimeAsync(0);
    });

    unmount();
    vi.mocked(api.getExportProgress).mockClear();
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });

    expect(api.getExportProgress).not.toHaveBeenCalled();
  });

  it('a response that arrives after the card was dismissed does not bring it back', async () => {
    vi.useFakeTimers();
    const pending = deferred<api.ExportProgress>();
    vi.mocked(api.getExportProgress).mockReturnValue(pending.promise);
    const { result } = renderHook(() => useExportFlow(t));

    act(() => result.current.startExportPolling('task-d', 'local'));
    act(() => result.current.dismissExportCard());
    await act(async () => {
      pending.resolve({ success: true, status: 'running', progress: 1, total: 3 });
      await vi.advanceTimersByTimeAsync(0);
    });

    expect(result.current.exportTask).toBeNull();
  });

  it('a start that lands after unmount only remembers the task and does not poll', async () => {
    vi.useFakeTimers();
    vi.mocked(api.getExportProgress).mockResolvedValue({ success: true, status: 'running', progress: 1, total: 3 });
    const { result, unmount } = renderHook(() => useExportFlow(t));
    const start = result.current.startExportPolling;
    unmount();

    act(() => start('task-late', 'local'));
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });

    expect(api.getExportProgress).not.toHaveBeenCalled();
    expect(JSON.parse(localStorage.getItem(ACTIVE_EXPORT_KEY)!)).toEqual({ id: 'task-late', mode: 'local' });
  });

  describe('a remembered task that cannot be used is dropped instead of polled', () => {
    it.each([
      ['is not valid JSON', 'not json{'],
      ['has a non-string id', JSON.stringify({ id: 5, mode: 'local' })],
      ['has an unknown mode', JSON.stringify({ id: 'x', mode: 'ftp' })],
      ['is an array', JSON.stringify(['x', 'local'])],
    ])('when it %s', (_case, raw) => {
      localStorage.setItem(ACTIVE_EXPORT_KEY, raw);

      renderHook(() => useExportFlow(t));

      expect(localStorage.getItem(ACTIVE_EXPORT_KEY)).toBeNull();
      expect(api.getExportProgress).not.toHaveBeenCalled();
    });
  });

  it('a new translate function mid-export keeps a single poll and still downloads a browser export', async () => {
    vi.useFakeTimers();
    let status: 'running' | 'done' = 'running';
    vi.mocked(api.getExportProgress).mockImplementation(async () => ({ success: true, status, progress: 1, total: 1 }));
    const clickSpy = spyOnDownloads();
    const { result, rerender } = renderHook(
      ({ translate }) => useExportFlow(translate),
      { initialProps: { translate: (key: string) => key } },
    );
    await act(async () => {
      result.current.startExportPolling('task-t', 'browser');
      await vi.advanceTimersByTimeAsync(0);
    });

    rerender({ translate: (key: string) => `[${key}]` }); // e.g. the UI language was switched

    status = 'done';
    vi.mocked(api.getExportProgress).mockClear();
    await act(async () => { await vi.advanceTimersByTimeAsync(1_500); });

    expect(api.getExportProgress).toHaveBeenCalledTimes(1);
    expect(clickSpy).toHaveBeenCalledTimes(1);
  });

  it('under StrictMode a remembered task is still polled by a single interval', async () => {
    vi.useFakeTimers();
    localStorage.setItem(ACTIVE_EXPORT_KEY, JSON.stringify({ id: 'task-s', mode: 'local' }));
    vi.mocked(api.getExportProgress).mockResolvedValue({ success: true, status: 'running', progress: 1, total: 3 });
    const { result } = renderHook(() => useExportFlow(t), { wrapper: StrictMode });
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(result.current.exportTask?.id).toBe('task-s');

    vi.mocked(api.getExportProgress).mockClear();
    await act(async () => { await vi.advanceTimersByTimeAsync(1_500); });
    expect(api.getExportProgress).toHaveBeenCalledTimes(1);
  });
});
