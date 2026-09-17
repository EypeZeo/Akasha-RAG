import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, waitFor, act } from '@testing-library/react';
import { useBuildFlow } from './useBuildFlow';
import * as api from '../api';

vi.mock('../api');

const ACTIVE_BUILD_KEY = 'akasha:active_build';

const t = (key: string) => key;

interface SyncProgressResponse {
  success: boolean;
  status?: 'running' | 'done' | 'failed' | 'cancelled';
  progress?: number;
  total?: number;
  message?: string;
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(res => { resolve = res; });
  return { promise, resolve };
}

beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe('useBuildFlow', () => {
  it('1. starts polling into building=true, then picks up real progress from the first tick', async () => {
    vi.mocked(api.getSyncProgress).mockResolvedValue({ success: true, status: 'running', progress: 2, total: 5 });
    const onBuildComplete = vi.fn();
    const { result } = renderHook(() => useBuildFlow(t, onBuildComplete));

    act(() => {
      result.current.startBuildPolling('task-1', '视频');
    });
    expect(result.current.building).toBe(true);

    await waitFor(() => {
      expect(result.current.buildProgress).toBe(2);
    });
    expect(result.current.buildTotal).toBe(5);
  });

  it('2. a thrown request does not affect polling and is not counted as a miss', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.mocked(api.getSyncProgress).mockRejectedValue(new Error('network blip'));
    const onBuildComplete = vi.fn();
    const { result } = renderHook(() => useBuildFlow(t, onBuildComplete));

    await act(async () => {
      result.current.startBuildPolling('task-2', '视频');
      await vi.advanceTimersByTimeAsync(0);
    });
    // Five consecutive thrown responses — well past the 2-miss self-heal
    // threshold, which would have fired if exceptions were wrongly counted.
    for (let i = 0; i < 5; i++) {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1500);
      });
    }
    expect(result.current.building).toBe(true);
    expect(onBuildComplete).not.toHaveBeenCalled();
  });

  it('3. exactly 2 consecutive success:false responses trigger self-heal, and it does not call onBuildComplete', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.mocked(api.getSyncProgress).mockResolvedValue({ success: false });
    const onBuildComplete = vi.fn();
    const { result } = renderHook(() => useBuildFlow(t, onBuildComplete));

    await act(async () => {
      result.current.startBuildPolling('task-3', '视频');
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(result.current.building).toBe(true); // 1 miss so far — not enough

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1500);
    });
    expect(result.current.building).toBe(false); // 2nd miss — self-heals
    expect(onBuildComplete).not.toHaveBeenCalled();
  });

  it('4. a successful response resets an already-counted miss (not decrements it)', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const responses: SyncProgressResponse[] = [
      { success: false }, // miss #1
      { success: true, status: 'running', progress: 1, total: 10 }, // resets to 0
      { success: false }, // miss #1 again (not #3)
    ];
    let call = 0;
    vi.mocked(api.getSyncProgress).mockImplementation(async () => responses[Math.min(call++, responses.length - 1)]);
    const onBuildComplete = vi.fn();
    const { result } = renderHook(() => useBuildFlow(t, onBuildComplete));

    await act(async () => {
      result.current.startBuildPolling('task-4', '视频');
      await vi.advanceTimersByTimeAsync(0);
    });
    await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
    await act(async () => { await vi.advanceTimersByTimeAsync(1500); });

    // Three responses consumed (1 miss, 1 success resetting it, 1 miss again)
    // — self-heal needs 2 *consecutive* misses, so it must not have fired.
    expect(result.current.building).toBe(true);
    expect(onBuildComplete).not.toHaveBeenCalled();
  });

  it('5. a task that completes immediately is caught by the synchronous first tick, before the 1500ms interval', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.mocked(api.getSyncProgress).mockResolvedValue({ success: true, status: 'done', total: 10 });
    const onBuildComplete = vi.fn();
    const { result } = renderHook(() => useBuildFlow(t, onBuildComplete));

    await act(async () => {
      result.current.startBuildPolling('task-5', '视频');
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(result.current.buildMessage).toBe('ingestCompleted');
    expect(result.current.buildTotal).toBe(10);
    expect(result.current.building).toBe(true); // still in terminal-pending-teardown
    expect(vi.mocked(api.getSyncProgress).mock.calls.length).toBe(1);
  });

  it('6. building stays true at 899ms, flips false at 900ms, onBuildComplete fires exactly once', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.mocked(api.getSyncProgress).mockResolvedValue({ success: true, status: 'done', total: 10 });
    const onBuildComplete = vi.fn();
    const { result } = renderHook(() => useBuildFlow(t, onBuildComplete));

    await act(async () => {
      result.current.startBuildPolling('task-6', '视频');
      await vi.advanceTimersByTimeAsync(0);
    });
    await act(async () => { await vi.advanceTimersByTimeAsync(899); });
    expect(result.current.building).toBe(true);

    await act(async () => { await vi.advanceTimersByTimeAsync(1); });
    expect(result.current.building).toBe(false);
    expect(onBuildComplete).toHaveBeenCalledTimes(1);
  });

  it('7. after teardown the interval is genuinely cleared, not just visually stopped', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.mocked(api.getSyncProgress).mockResolvedValue({ success: true, status: 'done', total: 10 });
    const onBuildComplete = vi.fn();
    const { result } = renderHook(() => useBuildFlow(t, onBuildComplete));

    await act(async () => {
      result.current.startBuildPolling('task-7', '视频');
      await vi.advanceTimersByTimeAsync(900);
    });
    expect(result.current.building).toBe(false);

    vi.mocked(api.getSyncProgress).mockClear();
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
    expect(api.getSyncProgress).not.toHaveBeenCalled();
  });

  it('8a. a stale response from a superseded task does not overwrite the new task\'s progress', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const deferredA = deferred<SyncProgressResponse>();
    vi.mocked(api.getSyncProgress).mockImplementation(async (taskId: string) => {
      if (taskId === 'task-a') return deferredA.promise;
      return { success: true, status: 'running', progress: 1, total: 10 };
    });
    const onBuildComplete = vi.fn();
    const { result } = renderHook(() => useBuildFlow(t, onBuildComplete));

    await act(async () => {
      result.current.startBuildPolling('task-a', '视频'); // fires the pending A tick
      await vi.advanceTimersByTimeAsync(0);
    });
    await act(async () => {
      result.current.startBuildPolling('task-b', '视频'); // supersedes A before it resolves
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(result.current.buildTaskId).toBe('task-b');
    expect(result.current.buildProgress).toBe(1);
    expect(result.current.buildTotal).toBe(10);

    await act(async () => {
      deferredA.resolve({ success: true, status: 'running', progress: 99, total: 100 });
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(result.current.buildTaskId).toBe('task-b');
    expect(result.current.buildProgress).toBe(1);
    expect(result.current.buildTotal).toBe(10);
  });

  it('8b. a stale terminal response from a superseded task cannot kill the new task\'s polling', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const deferredA = deferred<SyncProgressResponse>();
    vi.mocked(api.getSyncProgress).mockImplementation(async (taskId: string) => {
      if (taskId === 'task-a') return deferredA.promise;
      return { success: true, status: 'running', progress: 1, total: 10 };
    });
    const onBuildComplete = vi.fn();
    const { result } = renderHook(() => useBuildFlow(t, onBuildComplete));

    await act(async () => {
      result.current.startBuildPolling('task-a', '视频');
      await vi.advanceTimersByTimeAsync(0);
    });
    await act(async () => {
      result.current.startBuildPolling('task-b', '视频');
      await vi.advanceTimersByTimeAsync(0);
    });

    await act(async () => {
      deferredA.resolve({ success: true, status: 'done', total: 100 }); // A's stale terminal response
      await vi.advanceTimersByTimeAsync(0);
    });
    // B must still be actively polling: building stays true, task id unchanged,
    // and B's own 900ms teardown has not fired since it hasn't reached a terminal.
    expect(result.current.building).toBe(true);
    expect(result.current.buildTaskId).toBe('task-b');
    expect(onBuildComplete).not.toHaveBeenCalled();

    vi.mocked(api.getSyncProgress).mockClear();
    await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
    // B's interval must still be alive — A's stale terminal must not have
    // cleared it.
    expect(api.getSyncProgress).toHaveBeenCalledWith('task-b');
  });

  it('9. two overlapping ticks of the same task both reaching terminal status only tear down once', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const deferred1 = deferred<SyncProgressResponse>();
    const deferred2 = deferred<SyncProgressResponse>();
    vi.mocked(api.getSyncProgress)
      .mockImplementationOnce(async () => deferred1.promise)
      .mockImplementationOnce(async () => deferred2.promise);
    const onBuildComplete = vi.fn();
    const { result } = renderHook(() => useBuildFlow(t, onBuildComplete));

    act(() => {
      result.current.startBuildPolling('task-9', '视频'); // fires tick #1 synchronously (deferred1)
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1500); // interval fires tick #2 (deferred2), #1 still pending
    });

    await act(async () => {
      deferred1.resolve({ success: true, status: 'done', total: 10 }); // seizes the terminal teardown
      await vi.advanceTimersByTimeAsync(0);
    });
    await act(async () => {
      deferred2.resolve({ success: true, status: 'done', total: 10 }); // arrives after the seize, must be a no-op
      await vi.advanceTimersByTimeAsync(0);
    });

    await act(async () => { await vi.advanceTimersByTimeAsync(900); });

    expect(onBuildComplete).toHaveBeenCalledTimes(1);
    expect(localStorage.getItem(ACTIVE_BUILD_KEY)).toBeNull();
  });

  it('10. a response that arrives after unmount produces no side effects', async () => {
    const pending = deferred<SyncProgressResponse>();
    vi.mocked(api.getSyncProgress).mockImplementationOnce(async () => pending.promise);
    const onBuildComplete = vi.fn();
    const { result, unmount } = renderHook(() => useBuildFlow(t, onBuildComplete));

    act(() => {
      result.current.startBuildPolling('task-10', '视频');
    });
    const savedBeforeUnmount = localStorage.getItem(ACTIVE_BUILD_KEY);
    expect(savedBeforeUnmount).not.toBeNull();

    unmount();

    await act(async () => {
      pending.resolve({ success: true, status: 'done', total: 10 });
      await Promise.resolve();
    });

    expect(onBuildComplete).not.toHaveBeenCalled();
    expect(localStorage.getItem(ACTIVE_BUILD_KEY)).toBe(savedBeforeUnmount);
  });

  it('11. the completion callback used is the one captured at task start, not a later re-render\'s new reference', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.mocked(api.getSyncProgress).mockResolvedValue({ success: true, status: 'done', total: 10 });
    const cb1 = vi.fn();
    const cb2 = vi.fn();
    const { result, rerender } = renderHook(
      ({ onBuildComplete }) => useBuildFlow(t, onBuildComplete),
      { initialProps: { onBuildComplete: cb1 } },
    );

    await act(async () => {
      result.current.startBuildPolling('task-11', '视频');
      await vi.advanceTimersByTimeAsync(0);
    });

    rerender({ onBuildComplete: cb2 }); // simulates a parent re-render handing in a new callback identity

    await act(async () => { await vi.advanceTimersByTimeAsync(900); });

    expect(cb1).toHaveBeenCalledTimes(1);
    expect(cb2).not.toHaveBeenCalled();
  });

  it('12. a successful cancel sets cancelling and updates the message, but polling keeps running', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.mocked(api.getSyncProgress).mockResolvedValue({ success: true, status: 'running', progress: 1, total: 10 });
    vi.mocked(api.cancelSync).mockResolvedValue({ success: true });
    const onBuildComplete = vi.fn();
    const { result } = renderHook(() => useBuildFlow(t, onBuildComplete));

    await act(async () => {
      result.current.startBuildPolling('task-12', '视频');
      await vi.advanceTimersByTimeAsync(0);
    });

    await act(async () => {
      await result.current.handleCancelBuild();
    });
    expect(result.current.cancelling).toBe(true);
    expect(result.current.buildMessage).toBe('cancelling');
    expect(api.cancelSync).toHaveBeenCalledWith('task-12');

    vi.mocked(api.getSyncProgress).mockClear();
    await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
    expect(api.getSyncProgress).toHaveBeenCalledWith('task-12'); // tick keeps firing after cancel is requested
  });

  it('13. a failed cancel bounces cancelling back to false without touching the active task', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.mocked(api.getSyncProgress).mockResolvedValue({ success: true, status: 'running', progress: 1, total: 10 });
    vi.mocked(api.cancelSync).mockRejectedValue(new Error('cancel failed'));
    vi.spyOn(window, 'alert').mockImplementation(() => {});
    vi.spyOn(console, 'error').mockImplementation(() => {});
    const onBuildComplete = vi.fn();
    const { result } = renderHook(() => useBuildFlow(t, onBuildComplete));

    await act(async () => {
      result.current.startBuildPolling('task-13', '视频');
      await vi.advanceTimersByTimeAsync(0);
    });

    await act(async () => {
      await result.current.handleCancelBuild();
    });
    expect(result.current.cancelling).toBe(false);
    expect(result.current.buildTaskId).toBe('task-13');
  });

  it('14. cancel is a no-op when there is no active task, or when a cancel is already in flight', async () => {
    vi.mocked(api.cancelSync).mockResolvedValue({ success: true });
    const onBuildComplete = vi.fn();
    const { result } = renderHook(() => useBuildFlow(t, onBuildComplete));

    await act(async () => {
      await result.current.handleCancelBuild(); // no buildTaskId yet
    });
    expect(api.cancelSync).not.toHaveBeenCalled();

    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.mocked(api.getSyncProgress).mockResolvedValue({ success: true, status: 'running', progress: 1, total: 10 });
    const { promise, resolve } = deferred<{ success: boolean }>();
    vi.mocked(api.cancelSync).mockReturnValue(promise);
    await act(async () => {
      result.current.startBuildPolling('task-14', '视频');
      await vi.advanceTimersByTimeAsync(0);
    });

    act(() => { result.current.handleCancelBuild(); }); // first call: cancelling flips to true synchronously-ish
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    act(() => { result.current.handleCancelBuild(); }); // second call while the first is still in flight: guarded

    expect(api.cancelSync).toHaveBeenCalledTimes(1);

    await act(async () => {
      resolve({ success: true });
      await promise;
    });
  });

  it('15. F5 restore resumes polling with the "restoring" message, not "starting"', async () => {
    localStorage.setItem(ACTIVE_BUILD_KEY, JSON.stringify({ task_id: 'task-15', typeLabel: '视频' }));
    vi.mocked(api.getSyncProgress).mockResolvedValue({ success: true, status: 'running', progress: 3, total: 10 });
    const onBuildComplete = vi.fn();
    const { result } = renderHook(() => useBuildFlow(t, onBuildComplete));

    await waitFor(() => {
      expect(result.current.buildTaskId).toBe('task-15');
    });
    expect(result.current.buildMessage).toBe('ingestRestoring');
    expect(api.getSyncProgress).toHaveBeenCalledWith('task-15');
  });

  describe('16. corrupted ACTIVE_BUILD_KEY data is cleaned up on mount instead of silently ignored forever', () => {
    it('16a. invalid JSON', async () => {
      localStorage.setItem(ACTIVE_BUILD_KEY, 'not json{');
      const { result } = renderHook(() => useBuildFlow(t, vi.fn()));
      await waitFor(() => {
        expect(localStorage.getItem(ACTIVE_BUILD_KEY)).toBeNull();
      });
      expect(result.current.building).toBe(false);
      expect(api.getSyncProgress).not.toHaveBeenCalled();
    });

    it('16b. empty string', async () => {
      localStorage.setItem(ACTIVE_BUILD_KEY, '');
      renderHook(() => useBuildFlow(t, vi.fn()));
      await waitFor(() => {
        expect(localStorage.getItem(ACTIVE_BUILD_KEY)).toBeNull();
      });
      expect(api.getSyncProgress).not.toHaveBeenCalled();
    });

    it('16c. valid JSON but task_id is not a string (number)', async () => {
      localStorage.setItem(ACTIVE_BUILD_KEY, JSON.stringify({ task_id: 12345, typeLabel: '视频' }));
      renderHook(() => useBuildFlow(t, vi.fn()));
      await waitFor(() => {
        expect(localStorage.getItem(ACTIVE_BUILD_KEY)).toBeNull();
      });
      expect(api.getSyncProgress).not.toHaveBeenCalled();
    });

    it('16d. valid JSON but the overall shape is wrong (an array)', async () => {
      localStorage.setItem(ACTIVE_BUILD_KEY, JSON.stringify(['task-x', '视频']));
      renderHook(() => useBuildFlow(t, vi.fn()));
      await waitFor(() => {
        expect(localStorage.getItem(ACTIVE_BUILD_KEY)).toBeNull();
      });
      expect(api.getSyncProgress).not.toHaveBeenCalled();
    });
  });

  it('17. startBuildPolling called after unmount only persists ACTIVE_BUILD_KEY, does not poll or write state', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.mocked(api.getSyncProgress).mockResolvedValue({ success: true, status: 'running', progress: 1, total: 10 });
    const onBuildComplete = vi.fn();
    const { result, unmount } = renderHook(() => useBuildFlow(t, onBuildComplete));
    const startBuildPollingRef = result.current.startBuildPolling;

    unmount();

    act(() => {
      startBuildPollingRef('task-17', '视频');
    });

    const saved = localStorage.getItem(ACTIVE_BUILD_KEY);
    expect(saved).not.toBeNull();
    expect(JSON.parse(saved!).task_id).toBe('task-17');

    await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
    expect(api.getSyncProgress).not.toHaveBeenCalled();
  });
});
