import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import ChatSessionDrawer from './ChatSessionDrawer';
import { I18nProvider, TRANSLATIONS } from '../i18n';
import * as api from '../api';

vi.mock('../api');

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(done => { resolve = done; });
  return { promise, resolve };
}
const session = (title: string): api.SessionItem => ({
  id: 1, title, created_at: '', last_message_at: null, message_count: 1,
});
const renderDrawer = (refreshKey = 0, onSessionDeleted = vi.fn()) => (
  <I18nProvider>
    <ChatSessionDrawer refreshKey={refreshKey} onSessionDeleted={onSessionDeleted} onNewChat={vi.fn()} />
  </I18nProvider>
);

beforeEach(() => {
  vi.mocked(api.listSessions).mockReset();
});
afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe('chat history request ordering', () => {
  it('aborts an old search and ignores its late result', async () => {
    vi.useFakeTimers();
    const old = deferred<Awaited<ReturnType<typeof api.listSessions>>>();
    vi.mocked(api.listSessions).mockReturnValueOnce(old.promise)
      .mockResolvedValue({ success: true, items: [session('New search result')] });
    const { unmount } = render(renderDrawer());
    const signal = vi.mocked(api.listSessions).mock.calls[0][1]!;
    fireEvent.change(screen.getByPlaceholderText(TRANSLATIONS.en.searchChats), { target: { value: 'new' } });
    await act(async () => { await vi.advanceTimersByTimeAsync(250); });
    expect(signal.aborted).toBe(true);
    expect(screen.getByText('New search result')).toBeTruthy();
    await act(async () => { old.resolve({ success: true, items: [session('Stale result')] }); });
    expect(screen.queryByText('Stale result')).toBeNull();
    expect(screen.getByText('New search result')).toBeTruthy();
    unmount();
  });

  it('does not let a stale refresh resurrect a deleted session', async () => {
    const stale = deferred<Awaited<ReturnType<typeof api.listSessions>>>();
    vi.mocked(api.listSessions).mockResolvedValueOnce({ success: true, items: [session('Delete me')] })
      .mockReturnValueOnce(stale.promise).mockResolvedValue({ success: true, items: [] });
    vi.mocked(api.deleteSession).mockResolvedValue({ success: true });
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    const deleted = vi.fn();
    const { rerender } = render(renderDrawer(0, deleted));
    await screen.findByText('Delete me');
    rerender(renderDrawer(1, deleted));
    const staleSignal = vi.mocked(api.listSessions).mock.calls[1][1]!;
    await act(async () => { fireEvent.click(screen.getByTitle(TRANSLATIONS.en.deleteChat)); });
    expect(deleted).toHaveBeenCalledTimes(1);
    expect(staleSignal.aborted).toBe(true);
    await act(async () => { stale.resolve({ success: true, items: [session('Delete me')] }); });
    expect(screen.queryByText('Delete me')).toBeNull();
  });
});
