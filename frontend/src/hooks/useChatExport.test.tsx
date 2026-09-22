import { act, renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { useChatExport } from './useChatExport';
import * as history from '../utils/chatHistory';
import * as exporters from '../utils/chatExport';
import type { Message } from '../components/ChatMessageRow';

vi.mock('../utils/chatHistory', async importOriginal => {
  const actual = await importOriginal<typeof history>();
  return { ...actual, fetchExportHistory: vi.fn() };
});
vi.mock('../utils/chatExport');
const t = (key: string) => key;
const labels = { locale: 'en', heading: 'h', topic: 't', exportedAt: 'at', messageCount: 'n', you: 'you', assistant: 'ai', system: 'sys', latency: 'l', sources: 's', match: 'm', footer: 'f' };
const deferred = <T,>() => { let resolve!: (value: T) => void; const promise = new Promise<T>(r => { resolve = r; }); return { promise, resolve }; };

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  document.body.classList.remove('chat-printing');
});

describe('useChatExport task ownership', () => {
  it('an old cancelled task cannot export or clear a newer task', async () => {
    const a = deferred<Message[]>();
    const b = deferred<Message[]>();
    vi.mocked(history.fetchExportHistory).mockReturnValueOnce(a.promise).mockReturnValueOnce(b.promise);
    const { result } = renderHook(() => useChatExport(t));
    act(() => result.current.start('markdown', 1, [], 'A', labels));
    act(() => result.current.cancel());
    act(() => result.current.start('markdown', 2, [], 'B', labels));
    await act(async () => { a.resolve([{ clientKey: 'a', role: 'user', content: 'A' }]); });
    expect(exporters.exportChatToMarkdown).not.toHaveBeenCalled();
    expect(result.current.progress).toBe(0);
    await act(async () => { b.resolve([{ clientKey: 'b', role: 'user', content: 'B' }]); });
    expect(exporters.exportChatToMarkdown).toHaveBeenCalledWith([expect.objectContaining({ content: 'B' })], 'B', labels);
    expect(result.current.progress).toBeNull();
  });

  it('cancels both animation frames before PDF printing', async () => {
    const frames: FrameRequestCallback[] = [];
    vi.spyOn(window, 'requestAnimationFrame').mockImplementation(cb => { frames.push(cb); return frames.length; });
    vi.spyOn(window, 'cancelAnimationFrame').mockImplementation(() => {});
    const print = vi.spyOn(window, 'print').mockImplementation(() => {});
    const { result } = renderHook(() => useChatExport(t));
    act(() => result.current.start('pdf', null, [{ clientKey: 'x', role: 'user', content: 'x' }], 'PDF', labels));
    expect(result.current.printSnapshot?.messages).toHaveLength(1);
    act(() => result.current.cancel());
    act(() => frames.splice(0).forEach(cb => cb(0)));
    expect(print).not.toHaveBeenCalled();
    expect(result.current.printSnapshot).toBeNull();
  });

  it('gives native printing ownership over a pending history export', async () => {
    const pending = deferred<Message[]>();
    vi.mocked(history.fetchExportHistory).mockReturnValue(pending.promise);
    const { result } = renderHook(() => useChatExport(t));
    act(() => {
      result.current.nativeSnapshotRef.current = () => ({
        messages: [{ clientKey: 'native', role: 'user', content: 'visible' }],
        title: 'Native', labels,
      });
      result.current.start('markdown', 1, [], 'Async', labels);
    });

    act(() => window.dispatchEvent(new Event('beforeprint')));
    expect(result.current.printSnapshot?.title).toBe('Native');
    expect(result.current.progress).toBeNull();
    await act(async () => {
      pending.resolve([{ clientKey: 'late', role: 'user', content: 'late' }]);
      await Promise.resolve();
    });
    expect(exporters.exportChatToMarkdown).not.toHaveBeenCalled();
    expect(result.current.printSnapshot?.title).toBe('Native');

    act(() => window.dispatchEvent(new Event('afterprint')));
    expect(result.current.printSnapshot).toBeNull();
  });

  it('keeps a programmatic print document mounted until afterprint', () => {
    vi.useFakeTimers();
    const frames: FrameRequestCallback[] = [];
    vi.spyOn(window, 'requestAnimationFrame').mockImplementation(cb => { frames.push(cb); return frames.length; });
    vi.spyOn(window, 'cancelAnimationFrame').mockImplementation(() => {});
    const print = vi.spyOn(window, 'print').mockImplementation(() => {});
    const { result } = renderHook(() => useChatExport(t));

    act(() => result.current.start('pdf', null, [{ clientKey: 'x', role: 'user', content: 'x' }], 'PDF', labels));
    act(() => { frames.shift()?.(0); });
    act(() => { frames.shift()?.(0); });
    expect(print).toHaveBeenCalledTimes(1);
    expect(result.current.printSnapshot?.title).toBe('PDF');
    act(() => { vi.advanceTimersByTime(1_000); });
    expect(result.current.printSnapshot?.title).toBe('PDF');

    act(() => window.dispatchEvent(new Event('afterprint')));
    expect(result.current.printSnapshot).toBeNull();
  });
});

const oneMessage: Message = { clientKey: 'x', role: 'user', content: 'x' };
const manyMessages = (count: number): Message[] => Array.from({ length: count }, (_, i) => ({ clientKey: `k${i}`, role: 'user', content: 'x' }));

function controlFrames() {
  const frames: FrameRequestCallback[] = [];
  vi.spyOn(window, 'requestAnimationFrame').mockImplementation(cb => { frames.push(cb); return frames.length; });
  vi.spyOn(window, 'cancelAnimationFrame').mockImplementation(() => {});
  const print = vi.spyOn(window, 'print').mockImplementation(() => {});
  return { print, runNextFrame: () => act(() => { frames.shift()?.(0); }) };
}

function stubAlert() {
  vi.spyOn(console, 'error').mockImplementation(() => {});
  return vi.spyOn(window, 'alert').mockImplementation(() => {});
}

describe('useChatExport print document lifetime', () => {
  it('does not print when the panel unmounts between the two animation frames', () => {
    const { print, runNextFrame } = controlFrames();
    const { result, unmount } = renderHook(() => useChatExport(t));
    act(() => result.current.start('pdf', null, [oneMessage], 'PDF', labels));
    runNextFrame();

    unmount();
    runNextFrame();

    expect(print).not.toHaveBeenCalled();
    expect(document.body.classList.contains('chat-printing')).toBe(false);
  });

  it('does not print when the session changes between the two animation frames', () => {
    const { print, runNextFrame } = controlFrames();
    const { result } = renderHook(() => useChatExport(t));
    act(() => result.current.start('pdf', null, [oneMessage], 'PDF', labels));
    runNextFrame();

    act(() => result.current.invalidate());
    runNextFrame();

    expect(print).not.toHaveBeenCalled();
    expect(document.body.classList.contains('chat-printing')).toBe(false);
  });

  it('takes the print document down 60 s after print() when the browser never reports afterprint', () => {
    vi.useFakeTimers();
    const { print, runNextFrame } = controlFrames();
    const { result } = renderHook(() => useChatExport(t));
    act(() => result.current.start('pdf', null, [oneMessage], 'PDF', labels));
    runNextFrame();
    runNextFrame();
    expect(print).toHaveBeenCalledTimes(1);

    act(() => { vi.advanceTimersByTime(59_999); });
    expect(result.current.printSnapshot).not.toBeNull();
    act(() => { vi.advanceTimersByTime(1); });

    expect(result.current.printSnapshot).toBeNull();
    expect(document.body.classList.contains('chat-printing')).toBe(false);
  });

  it('reports a print() that throws, and removes the print document', () => {
    const alertSpy = stubAlert();
    const { print, runNextFrame } = controlFrames();
    print.mockImplementation(() => { throw new Error('blocked'); });
    const { result } = renderHook(() => useChatExport(t));
    act(() => result.current.start('pdf', null, [oneMessage], 'PDF', labels));
    runNextFrame();
    runNextFrame();

    expect(alertSpy).toHaveBeenCalledWith('operationFailed');
    expect(result.current.printSnapshot).toBeNull();
    expect(document.body.classList.contains('chat-printing')).toBe(false);
  });

  it('refuses a PDF over the print limits, whatever the file limits allow', () => {
    const alertSpy = stubAlert();
    const { print } = controlFrames();
    const { result } = renderHook(() => useChatExport(t));

    act(() => result.current.start('pdf', null, manyMessages(history.PRINT_LIMITS.messages + 1), 'PDF', labels));

    expect(alertSpy).toHaveBeenCalledWith('exportTooLarge');
    expect(result.current.printSnapshot).toBeNull();
    expect(print).not.toHaveBeenCalled();
  });
});

describe('useChatExport native printing (Ctrl+P)', () => {
  it('prints the loaded window under a notice that says so, and removes it afterwards', () => {
    const { result } = renderHook(() => useChatExport(t));
    act(() => {
      result.current.nativeSnapshotRef.current = () => ({
        messages: manyMessages(2), title: 'Native', labels, notice: 'loaded messages only',
      });
    });

    act(() => window.dispatchEvent(new Event('beforeprint')));
    expect(result.current.printSnapshot).toMatchObject({ title: 'Native', notice: 'loaded messages only' });
    expect(result.current.printSnapshot?.messages).toHaveLength(2);
    expect(document.body.classList.contains('chat-printing')).toBe(true);

    act(() => window.dispatchEvent(new Event('afterprint')));
    expect(result.current.printSnapshot).toBeNull();
    expect(document.body.classList.contains('chat-printing')).toBe(false);
  });

  it('prints only the too-large notice, never a truncated document, when the loaded window exceeds the print limits', () => {
    const { result } = renderHook(() => useChatExport(t));
    act(() => {
      result.current.nativeSnapshotRef.current = () => ({
        messages: manyMessages(history.PRINT_LIMITS.messages + 1), title: 'Native', labels, notice: 'loaded messages only',
      });
    });

    act(() => window.dispatchEvent(new Event('beforeprint')));

    expect(result.current.printSnapshot?.messages).toEqual([]);
    expect(result.current.printSnapshot?.notice).toBe('exportTooLarge');
  });

  it('leaves the page alone when the panel has nothing to print', () => {
    const { result } = renderHook(() => useChatExport(t));
    act(() => { result.current.nativeSnapshotRef.current = () => null; });

    act(() => window.dispatchEvent(new Event('beforeprint')));

    expect(result.current.printSnapshot).toBeNull();
    expect(document.body.classList.contains('chat-printing')).toBe(false);
  });
});

describe('useChatExport export flow', () => {
  it.each([
    ['markdown', 'exportChatToMarkdown'],
    ['word', 'exportChatToWord'],
    ['text', 'exportChatToText'],
  ] as const)('%s hands the merged history to %s once and clears the progress', async (format, exporter) => {
    vi.mocked(history.fetchExportHistory).mockResolvedValue([oneMessage]);
    const { result } = renderHook(() => useChatExport(t));

    await act(async () => { result.current.start(format, 1, [], 'Title', labels); });

    expect(exporters[exporter]).toHaveBeenCalledTimes(1);
    expect(exporters[exporter]).toHaveBeenCalledWith([oneMessage], 'Title', labels);
    expect(result.current.progress).toBeNull();
  });

  it('shows the number of messages fetched so far', async () => {
    const gate = deferred<Message[]>();
    vi.mocked(history.fetchExportHistory).mockImplementation(async (_id, _loaded, _signal, onProgress) => {
      onProgress(200);
      return gate.promise;
    });
    const { result } = renderHook(() => useChatExport(t));

    await act(async () => { result.current.start('markdown', 1, [], 'T', labels); });
    expect(result.current.progress).toBe(200);

    await act(async () => { gate.resolve([oneMessage]); });
    expect(result.current.progress).toBeNull();
  });

  it('reports a failed history fetch, exports nothing, and lets the user retry', async () => {
    const alertSpy = stubAlert();
    vi.mocked(history.fetchExportHistory).mockRejectedValueOnce(new Error('network')).mockResolvedValueOnce([oneMessage]);
    const { result } = renderHook(() => useChatExport(t));

    await act(async () => { result.current.start('markdown', 1, [], 'T', labels); });
    expect(alertSpy).toHaveBeenCalledWith('operationFailed');
    expect(exporters.exportChatToMarkdown).not.toHaveBeenCalled();
    expect(result.current.progress).toBeNull();

    await act(async () => { result.current.start('markdown', 1, [], 'T', labels); });
    expect(exporters.exportChatToMarkdown).toHaveBeenCalledTimes(1);
  });

  it.each([
    ['a client-side capacity error', () => new history.ExportCapacityError('Transcript too large')],
    ['an HTTP 413 from the server', () => new Error('413: History page exceeds byte limit')],
  ])('says the transcript is too large for %s', async (_case, makeError) => {
    const alertSpy = stubAlert();
    vi.mocked(history.fetchExportHistory).mockRejectedValue(makeError());
    const { result } = renderHook(() => useChatExport(t));

    await act(async () => { result.current.start('markdown', 1, [], 'T', labels); });

    expect(alertSpy).toHaveBeenCalledWith('exportTooLarge');
    expect(exporters.exportChatToMarkdown).not.toHaveBeenCalled();
  });

  it('refuses an oversized unsaved chat without contacting the server', () => {
    const alertSpy = stubAlert();
    const { result } = renderHook(() => useChatExport(t));

    act(() => result.current.start('markdown', null, manyMessages(history.EXPORT_LIMITS.messages + 1), 'T', labels));

    expect(alertSpy).toHaveBeenCalledWith('exportTooLarge');
    expect(history.fetchExportHistory).not.toHaveBeenCalled();
    expect(exporters.exportChatToMarkdown).not.toHaveBeenCalled();
    expect(result.current.progress).toBeNull();
  });

  it('ignores a second start while an export is running', () => {
    vi.mocked(history.fetchExportHistory).mockReturnValue(deferred<Message[]>().promise);
    const { result } = renderHook(() => useChatExport(t));

    act(() => result.current.start('markdown', 1, [], 'A', labels));
    act(() => result.current.start('word', 1, [], 'B', labels));

    expect(history.fetchExportHistory).toHaveBeenCalledTimes(1);
  });

  it('aborts the request when the panel unmounts, and exports nothing when the answer arrives later', async () => {
    let signal: AbortSignal | undefined;
    const pending = deferred<Message[]>();
    vi.mocked(history.fetchExportHistory).mockImplementation((_id, _loaded, requestSignal) => {
      signal = requestSignal;
      return pending.promise;
    });
    const { result, unmount } = renderHook(() => useChatExport(t));
    act(() => result.current.start('markdown', 1, [], 'A', labels));

    unmount();
    expect(signal?.aborted).toBe(true);
    await act(async () => { pending.resolve([oneMessage]); });

    expect(exporters.exportChatToMarkdown).not.toHaveBeenCalled();
  });
});
