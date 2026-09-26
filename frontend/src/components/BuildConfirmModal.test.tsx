import { StrictMode, type ComponentProps } from 'react';
import { describe, it, expect, vi, beforeEach, afterEach, type MockInstance } from 'vitest';
import { render, screen, waitFor, act, fireEvent } from '@testing-library/react';
import BuildConfirmModal from './BuildConfirmModal';
import { I18nProvider, TRANSLATIONS } from '../i18n';
import * as api from '../api';

vi.mock('../api');

describe('BuildConfirmModal', () => {
  beforeEach(() => {
    vi.mocked(api.listPendingKnowledge).mockResolvedValue({
      success: true, items: [], total: 0, video_count: 0, note_count: 0, page: 1, has_more: false,
    } as any);
  });

  it('the icon-only close button has an accessible name', async () => {
    render(
      <I18nProvider>
        <BuildConfirmModal onClose={vi.fn()} onConfirm={vi.fn()} pendingCount={0} />
      </I18nProvider>,
    );
    await waitFor(() => {
      expect(screen.getByRole('button', { name: TRANSLATIONS.en.close })).toBeTruthy();
    });
  });
});

// ---------------------------------------------------------------------------------------------------------
// Request scope. A response may only touch the list, the counts, the page cursor and the loading flags while
// the (collection, platform, tab) it was asked for is still the one on screen. Every call to
// listPendingKnowledge below stays pending until the test settles it, so each test decides the order in which
// responses arrive.
// ---------------------------------------------------------------------------------------------------------

type Props = ComponentProps<typeof BuildConfirmModal>;
type PendingResponse = Awaited<ReturnType<typeof api.listPendingKnowledge>>;

interface PendingCall {
  collectionId: string | null | undefined;
  contentType: string | undefined;
  page: number;
  platform: string | undefined;
  signal: AbortSignal | undefined;
  resolve: (response: PendingResponse) => void;
  reject: (error: unknown) => void;
}

const en = TRANSLATIONS.en;
const LOAD_MORE = /Scroll down or click to load 50 more/; // en.loadMoreHint, minus its counters

const video = (id: number, over: Partial<api.VideoItem> = {}): api.VideoItem => ({
  id, collection_id: 'c', platform_item_id: `pid-${id}`, url: `https://example.test/${id}`, title: `video ${id}`,
  author: 'author', duration: 60, item_type: 'video', status: 'pending', platform: 'douyin', ...over,
});
const note = (id: number): api.VideoItem => video(id, { title: `note ${id}`, item_type: 'note', duration: 0 });

const STATS = { total: 100, video_count: 60, note_count: 40 };
const pageOf = (items: api.VideoItem[], page: number, hasMore: boolean, stats = STATS): PendingResponse => ({
  success: true, items, ...stats, page, page_size: 50, has_more: hasMore,
});

function stubPending(): PendingCall[] {
  const calls: PendingCall[] = [];
  vi.mocked(api.listPendingKnowledge).mockImplementation(((...args: unknown[]) =>
    new Promise<PendingResponse>((resolve, reject) => {
      const [collectionId, contentType, page, , platform, signal] = args as [
        string | null | undefined, string | undefined, number | undefined, number | undefined,
        string | undefined, AbortSignal | undefined,
      ];
      calls.push({ collectionId, contentType, page: page ?? 1, platform, signal, resolve, reject });
    })) as never);
  return calls;
}

function renderModal(initial: Partial<Props> = {}, strict = false) {
  const onConfirm = vi.fn();
  const onClose = vi.fn();
  const element = (over: Partial<Props> = {}) => {
    const modal = (
      <I18nProvider>
        <BuildConfirmModal onClose={onClose} onConfirm={onConfirm} pendingCount={100} {...initial} {...over} />
      </I18nProvider>
    );
    return strict ? <StrictMode>{modal}</StrictMode> : modal;
  };
  const utils = render(element());
  return { ...utils, onConfirm, onClose, rerenderWith: (over: Partial<Props>) => utils.rerender(element(over)) };
}

const clickTab = (label: string) => fireEvent.click(screen.getByText(new RegExp(`${label}$`)));
const videoTab = () => clickTab(en.shortVideo);
const loadMore = () => fireEvent.click(screen.getByText(LOAD_MORE));
const confirmButton = () => screen.getByRole('button', { name: /^(Confirm Ingestion|Submitting)/ }) as HTMLButtonElement;
// the scrolling list: the rows are its direct children (jsdom has no layout, so every scroll counts as "at the bottom")
const listBox = () => screen.getByText('video 1').closest('button')!.parentElement!;
const settle = (fn: () => void) => act(async () => { fn(); });

describe('BuildConfirmModal request scope', () => {
  let errorSpy: MockInstance;

  beforeEach(() => {
    vi.mocked(api.listPendingKnowledge).mockReset();
    // the component logs a failed load; the tests below assert on what the user sees instead
    errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  afterEach(() => {
    errorSpy.mockRestore();
  });

  it('ignores a first-page response from the tab the user has already left', async () => {
    const calls = stubPending();
    renderModal();
    videoTab();
    expect(calls).toHaveLength(2); // [0] All, page 1 (abandoned)   [1] Videos, page 1
    await settle(() => calls[1].resolve(pageOf([video(11), video(12)], 1, false)));
    await settle(() => calls[0].resolve(pageOf([video(1), note(2)], 1, false, { total: 999, video_count: 999, note_count: 999 })));
    expect(screen.getByText('video 11')).toBeTruthy();
    expect(screen.queryByText('video 1')).toBeNull();
    expect(screen.queryByText('note 2')).toBeNull();
    expect(screen.queryByText('999')).toBeNull(); // the abandoned response's counts never reach the tabs
  });

  it('does not append a late "load more" page from the previous tab to the new tab', async () => {
    const calls = stubPending();
    renderModal();
    await settle(() => calls[0].resolve(pageOf([video(1), video(2)], 1, true)));
    loadMore(); // [1] All, page 2
    expect(calls[1]).toMatchObject({ contentType: 'all', page: 2 });
    videoTab(); // [2] Videos, page 1
    expect(calls[2]).toMatchObject({ contentType: 'video', page: 1 });
    await settle(() => calls[2].resolve(pageOf([video(11), video(12)], 1, true)));
    await settle(() => calls[1].resolve(pageOf([video(3), video(4)], 2, false))); // the late All page 2
    expect(screen.getByText('video 11')).toBeTruthy();
    expect(screen.queryByText('video 3')).toBeNull();
    expect(screen.queryByText('video 4')).toBeNull();
    // the Videos tab still has more to offer, and asks for its own page 2 (not page 3)
    loadMore();
    expect(calls[3]).toMatchObject({ contentType: 'video', page: 2 });
  });

  it('rejects a late page 2 of the old tab even while the new tab waits for its own page 2', async () => {
    const calls = stubPending();
    renderModal();
    await settle(() => calls[0].resolve(pageOf([video(1)], 1, true)));
    loadMore(); // [1] All, page 2
    videoTab(); // [2] Videos, page 1
    await settle(() => calls[2].resolve(pageOf([video(11)], 1, true)));
    loadMore(); // [3] Videos, page 2 — the same page number as the abandoned request
    expect(calls[3]).toMatchObject({ contentType: 'video', page: 2 });
    await settle(() => calls[1].resolve(pageOf([video(3)], 2, false)));
    expect(screen.queryByText('video 3')).toBeNull();
    await settle(() => calls[3].resolve(pageOf([video(12)], 2, false)));
    expect(screen.getByText('video 11')).toBeTruthy();
    expect(screen.getByText('video 12')).toBeTruthy();
    expect(screen.queryByText('video 3')).toBeNull();
  });

  it("an old request that fails late does not end the new tab's loading state", async () => {
    const calls = stubPending();
    renderModal();
    videoTab(); // [1] Videos, page 1 — still loading
    expect(screen.getByText(en.loadingFirstBatch)).toBeTruthy();
    await settle(() => calls[0].reject(new Error('boom'))); // the abandoned All request fails
    expect(screen.getByText(en.loadingFirstBatch)).toBeTruthy();
    expect(screen.queryByRole('alert')).toBeNull(); // the failure belongs to nobody on screen
    expect(confirmButton().disabled).toBe(true);
    // once the new tab's own page arrives, the list is there and nothing is left over from the abandoned failure
    await settle(() => calls[1].resolve(pageOf([video(11)], 1, false)));
    expect(screen.getByText('video 11')).toBeTruthy();
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('aborts the request of the tab it leaves, and every request on unmount', () => {
    const calls = stubPending();
    const { unmount } = renderModal();
    expect(calls[0].signal).toBeInstanceOf(AbortSignal);
    videoTab();
    expect(calls[0].signal!.aborted).toBe(true);
    expect(calls[1].signal!.aborted).toBe(false);
    unmount();
    expect(calls[1].signal!.aborted).toBe(true);
  });

  it('a change of collection or platform supersedes the in-flight request the same way', async () => {
    const calls = stubPending();
    const { rerenderWith } = renderModal({ collectionId: 'A', platform: 'douyin' });
    rerenderWith({ collectionId: 'B' });
    expect(calls[0].signal!.aborted).toBe(true);
    expect(calls[1]).toMatchObject({ collectionId: 'B', platform: 'douyin' });
    rerenderWith({ collectionId: 'B', platform: 'bilibili' });
    expect(calls[1].signal!.aborted).toBe(true);
    expect(calls[2]).toMatchObject({ collectionId: 'B', platform: 'bilibili' });
    await settle(() => calls[2].resolve(pageOf([video(21)], 1, false)));
    await settle(() => calls[0].resolve(pageOf([video(1)], 1, false))); // a response for collection A
    expect(screen.getByText('video 21')).toBeTruthy();
    expect(screen.queryByText('video 1')).toBeNull();
  });

  it('switching tabs drops a custom selection and returns to "everything in this tab"', async () => {
    const calls = stubPending();
    const { onConfirm } = renderModal();
    await settle(() => calls[0].resolve(pageOf([video(1), note(2)], 1, false)));
    fireEvent.click(screen.getByText('video 1')); // custom mode: everything except video 1
    expect(screen.getByText(/^1 selected/)).toBeTruthy();
    videoTab();
    await settle(() => calls[1].resolve(pageOf([video(11)], 1, false)));
    expect(screen.getByText(/^60 selected \(Range Mode\)/)).toBeTruthy();
    fireEvent.click(confirmButton());
    expect(onConfirm).toHaveBeenCalledWith([], 'video', 'all', undefined);
  });

  it('a failed first page says so, offers a retry and does not pretend the list is empty', async () => {
    const calls = stubPending();
    renderModal();
    await settle(() => calls[0].reject(new Error('offline')));
    expect(screen.getByRole('alert').textContent).toContain(en.operationFailed);
    expect(screen.queryByText(/No pending/)).toBeNull();
    expect(confirmButton().disabled).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: en.retry }));
    expect(calls).toHaveLength(2);
    expect(calls[1]).toMatchObject({ contentType: 'all', page: 1 });
    await settle(() => calls[1].resolve(pageOf([video(1)], 1, false)));
    expect(screen.queryByRole('alert')).toBeNull();
    expect(screen.getByText('video 1')).toBeTruthy();
    expect(confirmButton().disabled).toBe(false);
  });

  it('a response that says success: false is a failed load too', async () => {
    const calls = stubPending();
    renderModal();
    await settle(() => calls[0].resolve({ ...pageOf([], 1, false), success: false }));
    expect(screen.getByRole('alert').textContent).toContain(en.operationFailed);
    expect(confirmButton().disabled).toBe(true);
  });

  it('a failed "load more" keeps the loaded rows, says so, and can be retried', async () => {
    const calls = stubPending();
    renderModal();
    await settle(() => calls[0].resolve(pageOf([video(1)], 1, true)));
    loadMore();
    await settle(() => calls[1].reject(new Error('offline')));
    expect(screen.getByText('video 1')).toBeTruthy();
    expect(screen.getByRole('alert').textContent).toContain(en.operationFailed);
    loadMore(); // the button is still there and asks for the same page again
    expect(calls[2]).toMatchObject({ contentType: 'all', page: 2 });
    await settle(() => calls[2].resolve(pageOf([video(3)], 2, false)));
    expect(screen.queryByRole('alert')).toBeNull();
    expect(screen.getByText('video 1')).toBeTruthy();
    expect(screen.getByText('video 3')).toBeTruthy();
  });

  it('scrolling to the bottom loads the next page', async () => {
    const calls = stubPending();
    renderModal();
    await settle(() => calls[0].resolve(pageOf([video(1)], 1, true)));
    fireEvent.scroll(listBox());
    expect(calls[1]).toMatchObject({ contentType: 'all', page: 2 });
  });

  it('after a failed "load more", scrolling does not retry on its own; the button does', async () => {
    const calls = stubPending();
    renderModal();
    await settle(() => calls[0].resolve(pageOf([video(1)], 1, true)));
    fireEvent.scroll(listBox()); // [1] page 2
    await settle(() => calls[1].reject(new Error('offline')));
    fireEvent.scroll(listBox());
    fireEvent.scroll(listBox());
    expect(calls).toHaveLength(2); // still only the one failed attempt
    loadMore();
    expect(calls).toHaveLength(3);
  });

  it('the confirm button waits for the current tab to have loaded', async () => {
    const calls = stubPending();
    renderModal();
    expect(confirmButton().disabled).toBe(true); // first page still loading
    await settle(() => calls[0].resolve(pageOf([video(1)], 1, false)));
    expect(confirmButton().disabled).toBe(false);
    videoTab();
    expect(confirmButton().disabled).toBe(true); // the counts on screen are the old tab's
    await settle(() => calls[1].resolve(pageOf([video(11)], 1, false)));
    expect(confirmButton().disabled).toBe(false);
  });

  it('the confirm button stays available while a further page loads', async () => {
    const calls = stubPending();
    renderModal();
    await settle(() => calls[0].resolve(pageOf([video(1)], 1, true)));
    loadMore();
    expect(confirmButton().disabled).toBe(false);
  });

  it('shows both rows when two platforms share a platform_item_id, without duplicate-key warnings', async () => {
    const calls = stubPending();
    renderModal();
    await settle(() => calls[0].resolve(pageOf([
      video(1, { platform_item_id: 'same', platform: 'douyin', title: 'douyin same' }),
      video(2, { platform_item_id: 'same', platform: 'bilibili', title: 'bilibili same' }),
    ], 1, false)));
    expect(screen.getByText('douyin same')).toBeTruthy();
    expect(screen.getByText('bilibili same')).toBeTruthy();
    expect(errorSpy.mock.calls.some(args => String(args[0]).includes('same key'))).toBe(false);
  });

  it('still loads the first page under StrictMode (effect, cleanup, effect again)', async () => {
    const calls = stubPending();
    renderModal({}, true);
    expect(calls.length).toBeGreaterThanOrEqual(1);
    await settle(() => calls[calls.length - 1].resolve(pageOf([video(1)], 1, false)));
    expect(screen.getByText('video 1')).toBeTruthy();
    expect(confirmButton().disabled).toBe(false);
  });
});
