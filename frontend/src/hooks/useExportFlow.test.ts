import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, waitFor, act } from '@testing-library/react';
import { useExportFlow } from './useExportFlow';
import * as api from '../api';

vi.mock('../api');

const t = (key: string) => key;

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
});
