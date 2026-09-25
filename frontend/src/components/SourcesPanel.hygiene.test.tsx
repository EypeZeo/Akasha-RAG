/**
 * SourcesPanel hygiene (tracking issue #43): the global stats request has no "newest wins" rule, work that
 * finishes after the panel is gone still refreshes, notifies and pops alerts, and mixed-platform video rows
 * can share a React key. The second half locks scenarios the implementation already satisfies by design but
 * that had no test of their own: each of those tests states, in its name, what a regression would break.
 *
 * Tests marked "(guard)" pass on the unmodified code; they are here so the behaviour cannot silently regress.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, act, fireEvent, within, waitFor } from '@testing-library/react';
import SourcesPanel from './SourcesPanel';
import { I18nProvider, TRANSLATIONS } from '../i18n';
import * as api from '../api';
import { useWorkspaceStore } from '../store/workspace';

vi.mock('../api');

const en = TRANSLATIONS.en;

const collection = (over: Partial<api.CollectionItem> = {}): api.CollectionItem => ({
  id: 1, collection_id: 'col-1', title: 'Test Collection', video_count: 3, is_active: true, platform: 'douyin', ...over,
});

const video = (over: Partial<api.VideoItem> = {}): api.VideoItem => ({
  id: 1, collection_id: 'col-1', platform_item_id: 'v1', url: 'https://example.test/v1', title: 'Test Video',
  author: 'Author', duration: 30, item_type: 'video', status: 'done', platform: 'douyin', ...over,
});

const stats = (over: Record<string, unknown> = {}) => ({
  success: true,
  video_cache: { done: 3, pending: 3, failed: 0, downloading: 0, transcribing: 0 },
  detail: { video: { total: 3, done: 2, pending: 2 }, note: { total: 3, done: 1, pending: 1 } },
  ...over,
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

type VideosPage = { success: boolean; items: api.VideoItem[]; total: number; next_cursor?: string };

interface PanelProps {
  onBuildDone: () => void;
  selectedId: string;
  onSelectCollection: (id: string, owner?: string) => void;
  statsRefreshKey: number;
  collectionsPerPage: number;
  videosPerPage: number;
  onOpenSettings: () => void;
}

function setup(over: Partial<PanelProps> = {}) {
  const props: PanelProps = {
    onBuildDone: vi.fn(), selectedId: 'all', onSelectCollection: vi.fn(), statsRefreshKey: 0,
    collectionsPerPage: 20, videosPerPage: 20, onOpenSettings: vi.fn(), ...over,
  };
  const element = (next: Partial<PanelProps> = {}) => (
    <I18nProvider><SourcesPanel {...props} {...next} /></I18nProvider>
  );
  const utils = render(element());
  return { ...utils, props, rerenderWith: (next: Partial<PanelProps>) => utils.rerender(element(next)) };
}

/** Subscribes to the store the way SourcesStudio does, so a store-level platform switch reaches the panel. */
function StoreConnectedPanel() {
  const selectedId = useWorkspaceStore(s => s.selectedCollectionId);
  const owner = useWorkspaceStore(s => s.selectedCollectionPlatform);
  const select = useWorkspaceStore(s => s.setSelectedCollectionId);
  return (
    <I18nProvider>
      <SourcesPanel
        onBuildDone={vi.fn()} selectedId={selectedId} selectedOwner={owner ?? undefined} onSelectCollection={select}
        statsRefreshKey={0} collectionsPerPage={20} videosPerPage={20} onOpenSettings={vi.fn()}
      />
    </I18nProvider>
  );
}

const expandTestCollection = async () => fireEvent.click(await screen.findByText('Test Collection'));
/** Lets every promise continuation that is already queued (a handler with several awaits) run to its end. */
const flush = () => act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); });

/** Every listCollectionVideos call stays pending until the test settles it. */
function stubVideos() {
  const calls: Array<{ id: string; page: number; size: number; platform: string | undefined; cursor: string | undefined; d: ReturnType<typeof deferred<VideosPage>> }> = [];
  vi.mocked(api.listCollectionVideos).mockImplementation(((id: string, page: number, size: number, platform?: string, cursor?: string) => {
    const d = deferred<VideosPage>();
    calls.push({ id, page, size, platform, cursor, d });
    return d.promise;
  }) as never);
  return calls;
}

const clearVideoMocks = () => {
  vi.mocked(api.getKnowledgeStats).mockClear();
  vi.mocked(api.listCollections).mockClear();
  vi.mocked(api.listCollectionVideos).mockClear();
};

beforeEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
  useWorkspaceStore.setState({
    activeTab: 'sources', selectedCollectionId: 'all', selectedCollectionPlatform: null,
    selectedPlatform: 'all', activeSessionId: null,
  });
  vi.spyOn(window, 'confirm').mockReturnValue(true);
  vi.spyOn(window, 'alert').mockImplementation(() => {});
  vi.spyOn(console, 'error').mockImplementation(() => {});
  vi.mocked(api.listCollections).mockResolvedValue({ success: true, items: [collection()], total: 1 });
  vi.mocked(api.getKnowledgeStats).mockResolvedValue(stats());
  vi.mocked(api.listCollectionVideos).mockResolvedValue({ success: true, items: [], total: 0 });
  vi.mocked(api.getSettingsStatus).mockResolvedValue({ success: true, chat_ready: true, ingest_ready: true });
  vi.mocked(api.listPendingKnowledge).mockResolvedValue({
    success: true, items: [video({ status: 'pending' })], total: 1, video_count: 1, note_count: 0, page: 1, page_size: 50, has_more: false,
  });
});

afterEach(() => {
  vi.useRealTimers();
});

// =====================================================================================
// The global stats request: the newest one wins, and it never ends an operation
// =====================================================================================

describe('stats', () => {
  it('S1. an older stats request that answers last does not overwrite the newer numbers', async () => {
    const first = deferred<ReturnType<typeof stats>>();
    const second = deferred<ReturnType<typeof stats>>();
    vi.mocked(api.getKnowledgeStats).mockReset()
      .mockReturnValueOnce(first.promise as never).mockReturnValueOnce(second.promise as never)
      .mockResolvedValue(stats());
    const { rerenderWith } = setup(); // mount: request 1
    rerenderWith({ statsRefreshKey: 1 }); // the parent asks for a refresh: request 2
    expect(api.getKnowledgeStats).toHaveBeenCalledTimes(2);

    await act(async () => { second.resolve(stats({ video_cache: { done: 7, pending: 5, failed: 0 } })); });
    expect(screen.getByText(`${en.oneClickIngest} (5)`)).toBeTruthy();

    await act(async () => { first.resolve(stats({ video_cache: { done: 1, pending: 9, failed: 0 } })); }); // answers last
    expect(screen.getByText(`${en.oneClickIngest} (5)`)).toBeTruthy();
    expect(screen.queryByText(`${en.oneClickIngest} (9)`)).toBeNull();
  });

  it('S2. (guard) a stale stats response does not end a running sync, and the sync ends it exactly once', async () => {
    const mountStats = deferred<ReturnType<typeof stats>>();
    const sync = deferred<{ success: boolean }>();
    vi.mocked(api.getKnowledgeStats).mockReset().mockReturnValueOnce(mountStats.promise as never).mockResolvedValue(stats());
    vi.mocked(api.syncFavorites).mockReturnValue(sync.promise as never);
    setup();
    fireEvent.click(await screen.findByText(en.sync));
    expect(screen.getByText(en.syncing)).toBeTruthy();

    await act(async () => { mountStats.resolve(stats()); }); // the older, unrelated stats request answers now
    expect(screen.getByText(en.syncing)).toBeTruthy(); // the sync is still running

    await act(async () => { sync.resolve({ success: true }); });
    expect(screen.getByText(en.sync)).toBeTruthy();
    expect(window.alert).toHaveBeenCalledTimes(1);
  });
});

// =====================================================================================
// Work that finishes after the panel is gone: no refresh, no notification, no alert
// =====================================================================================

describe('follow-ups after unmount', () => {
  it('U1. a sync that succeeds after the panel is gone refreshes nothing and shows no alert', async () => {
    const sync = deferred<{ success: boolean }>();
    vi.mocked(api.syncFavorites).mockReturnValue(sync.promise as never);
    const { unmount } = setup();
    fireEvent.click(await screen.findByText(en.sync));
    unmount();
    clearVideoMocks();

    await act(async () => { sync.resolve({ success: true }); });

    expect(api.getKnowledgeStats).not.toHaveBeenCalled();
    expect(api.listCollections).not.toHaveBeenCalled();
    expect(window.alert).not.toHaveBeenCalled();
  });

  it('U1b. a sync that fails after the panel is gone shows no alert', async () => {
    const sync = deferred<{ success: boolean }>();
    vi.mocked(api.syncFavorites).mockReturnValue(sync.promise as never);
    const { unmount } = setup();
    fireEvent.click(await screen.findByText(en.sync));
    unmount();

    await act(async () => { sync.reject(new Error('offline')); });

    expect(window.alert).not.toHaveBeenCalled();
  });

  const deleteFirstVideo = async () => {
    vi.mocked(api.listCollectionVideos).mockResolvedValue({ success: true, items: [video({ status: 'done' })], total: 1 });
    const del = deferred<{ success: boolean }>();
    vi.mocked(api.deleteVideo).mockReturnValue(del.promise as never);
    const view = setup();
    await expandTestCollection();
    fireEvent.click(await screen.findByTitle(en.deleteIngestedTooltip));
    view.unmount();
    clearVideoMocks();
    return del;
  };

  it('U2. a delete that succeeds after the panel is gone refreshes nothing and shows no alert', async () => {
    const del = await deleteFirstVideo();
    await act(async () => { del.resolve({ success: true }); });
    expect(api.getKnowledgeStats).not.toHaveBeenCalled();
    expect(api.listCollectionVideos).not.toHaveBeenCalled();
    expect(window.alert).not.toHaveBeenCalled();
  });

  it('U2b. a delete that fails after the panel is gone shows no alert', async () => {
    const del = await deleteFirstVideo();
    await act(async () => { del.reject(new Error('offline')); });
    expect(window.alert).not.toHaveBeenCalled();
  });

  const clearWithTheCollectionListReady = async () => {
    const clear = deferred<{ success: boolean; reset_count: number }>();
    vi.mocked(api.clearAllKnowledge).mockReturnValue(clear.promise as never);
    const view = setup();
    await screen.findByText('Test Collection'); // the list is ready, so the clear has a scope to act on
    fireEvent.click(await screen.findByText(en.clearIngested));
    view.unmount();
    clearVideoMocks();
    return { clear, props: view.props };
  };

  it('U3. a clear that succeeds after the panel is gone refreshes nothing, notifies nobody and shows no alert', async () => {
    const { clear, props } = await clearWithTheCollectionListReady();
    await act(async () => { clear.resolve({ success: true, reset_count: 3 }); });
    expect(window.alert).not.toHaveBeenCalled();
    expect(api.getKnowledgeStats).not.toHaveBeenCalled();
    expect(api.listCollectionVideos).not.toHaveBeenCalled();
    expect(props.onBuildDone).not.toHaveBeenCalled();
  });

  it('U3b. a clear that fails after the panel is gone shows no alert', async () => {
    const { clear } = await clearWithTheCollectionListReady();
    await act(async () => { clear.reject(new Error('offline')); });
    expect(window.alert).not.toHaveBeenCalled();
  });

  const confirmAnIngest = async () => {
    const ingest = deferred<{ success: boolean; task_id?: string; pending_count?: number }>();
    vi.mocked(api.syncKnowledge).mockReturnValue(ingest.promise as never);
    const view = setup();
    await screen.findByText('Test Collection'); // the list is ready, so the ingest has a scope to act on
    fireEvent.click(await screen.findByText(`${en.oneClickIngest} (3)`));
    const confirm = await screen.findByRole('button', { name: /^Confirm Ingestion/ });
    // Wait for it to be enabled: the modal may keep it disabled until its first page has loaded.
    await waitFor(() => expect((confirm as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(confirm);
    view.unmount();
    return ingest;
  };

  it('U4. (guard) an ingest that was accepted after the panel is gone is still handed over: its task id is stored for the next mount, and nothing is announced', async () => {
    const ingest = await confirmAnIngest();
    await act(async () => { ingest.resolve({ success: true, task_id: 'b-1', pending_count: 1 }); });
    expect(JSON.parse(localStorage.getItem('akasha:active_build')!).task_id).toBe('b-1');
    expect(window.alert).not.toHaveBeenCalled();
  });

  it('U4b. an ingest that fails after the panel is gone shows no alert', async () => {
    const ingest = await confirmAnIngest();
    await act(async () => { ingest.reject(new Error('offline')); });
    expect(window.alert).not.toHaveBeenCalled();
  });

  const stuckPanelWithResetPending = async () => {
    vi.mocked(api.getKnowledgeStats).mockResolvedValue(stats({ video_cache: { done: 0, pending: 0, failed: 2 } }));
    const reset = deferred<{ success: boolean; reset_count: number }>();
    vi.mocked(api.resetFailedVideos).mockReturnValue(reset.promise as never);
    const view = setup();
    fireEvent.click(await screen.findByText(en.resetFailed));
    view.unmount();
    clearVideoMocks();
    return reset;
  };

  it('U5. a "reset failed" that finishes after the panel is gone refreshes nothing', async () => {
    const reset = await stuckPanelWithResetPending();
    await act(async () => { reset.resolve({ success: true, reset_count: 2 }); });
    expect(api.getKnowledgeStats).not.toHaveBeenCalled();
    expect(api.listCollectionVideos).not.toHaveBeenCalled();
  });

  it('U5b. a "reset failed" that is refused after the panel is gone shows no alert', async () => {
    const reset = await stuckPanelWithResetPending();
    await act(async () => { reset.resolve({ success: false, reset_count: 0 }); });
    expect(window.alert).not.toHaveBeenCalled();
  });
});

// =====================================================================================
// The ingest button's readiness check: only the newest click may open the modal
// =====================================================================================

describe('ingest readiness check', () => {
  it('M1. an older click whose readiness check answers late does not replace the scope of the newer click', async () => {
    vi.mocked(api.listCollections).mockResolvedValue({
      success: true, total: 2,
      items: [collection({ id: 1, collection_id: 'col-a', title: 'Collection A' }), collection({ id: 2, collection_id: 'col-b', title: 'Collection B' })],
    });
    const readyA = deferred<{ success: boolean; chat_ready: boolean; ingest_ready: boolean }>();
    const readyB = deferred<{ success: boolean; chat_ready: boolean; ingest_ready: boolean }>();
    vi.mocked(api.getSettingsStatus).mockReset()
      .mockReturnValueOnce(readyA.promise as never).mockReturnValueOnce(readyB.promise as never);
    setup();
    const ingest = () => fireEvent.click(screen.getByText(`${en.oneClickIngest} (3)`));

    fireEvent.click(await screen.findByText('Collection A'));
    await screen.findByText(`${en.oneClickIngest} (3)`);
    ingest(); // readiness check 1, for A
    fireEvent.click(screen.getByText('Collection B'));
    ingest(); // readiness check 2, for B

    await act(async () => { readyB.resolve({ success: true, chat_ready: true, ingest_ready: true }); });
    expect(within(await screen.findByRole('dialog')).getByText('Collection B')).toBeTruthy();

    await act(async () => { readyA.resolve({ success: true, chat_ready: true, ingest_ready: true }); }); // answers last

    expect(within(screen.getByRole('dialog')).getByText('Collection B')).toBeTruthy();
    expect(within(screen.getByRole('dialog')).queryByText('Collection A')).toBeNull();
    expect(vi.mocked(api.listPendingKnowledge).mock.calls.every(call => call[0] === 'col-b')).toBe(true);
  });
});

// =====================================================================================
// Row keys: one remote id can exist on both platforms
// =====================================================================================

describe('video rows', () => {
  it('K1. two platforms sharing a platform_item_id both render, without duplicate-key warnings', async () => {
    vi.mocked(api.listCollectionVideos).mockResolvedValue({
      success: true, total: 2,
      items: [
        video({ id: 1, platform_item_id: 'same', platform: 'douyin', title: 'Douyin same', status: 'pending' }),
        video({ id: 2, platform_item_id: 'same', platform: 'bilibili', title: 'Bilibili same', status: 'pending' }),
      ],
    });
    setup();
    await expandTestCollection();
    expect(await screen.findByText('Douyin same')).toBeTruthy();
    expect(screen.getByText('Bilibili same')).toBeTruthy();
    expect(vi.mocked(console.error).mock.calls.some(args => String(args[0]).includes('same key'))).toBe(false);
  });
});

// =====================================================================================
// Locks: scenarios the implementation satisfies by design (all pass on the unmodified code)
// =====================================================================================

describe('locks (guard)', () => {
  it('L1. changing the page size while a page is loading: the newest request wins, and the stale page neither shows nor leaves its cursor behind', async () => {
    const calls = stubVideos();
    setup();
    await expandTestCollection(); // [0] page 1, size 20
    await act(async () => {
      calls[0].d.resolve({ success: true, total: 100, next_cursor: 'c2', items: [video({ id: 1, platform_item_id: 'a', title: 'A page 1' })] });
    });
    fireEvent.click(screen.getByText(en.nextPage)); // [1] page 2, with the cursor page 1 handed out
    expect(calls[1]).toMatchObject({ page: 2, size: 20, cursor: 'c2' });
    fireEvent.change(screen.getByRole('combobox'), { target: { value: '50' } }); // [2] page 1 at size 50: supersedes [1]
    expect(calls[2]).toMatchObject({ page: 1, size: 50 });

    await act(async () => {
      calls[2].d.resolve({ success: true, total: 100, next_cursor: 'fresh-2', items: [video({ id: 3, platform_item_id: 'c', title: 'C at size 50' })] });
    });
    await act(async () => {
      calls[1].d.resolve({ success: true, total: 100, next_cursor: 'stale-3', items: [video({ id: 2, platform_item_id: 'b', title: 'B stale page 2' })] });
    }); // the superseded page answers last

    expect(screen.getByText('C at size 50')).toBeTruthy();
    expect(screen.queryByText('B stale page 2')).toBeNull();
    fireEvent.click(screen.getByText(en.nextPage)); // [3] page 2 at size 50 uses the fresh cursor
    expect(calls[3]).toMatchObject({ page: 2, size: 50, cursor: 'fresh-2' });
  });

  it('L2. a sync that finishes while the user is on page 2 refreshes page 2 (with its cursor), not page 1', async () => {
    const calls = stubVideos();
    vi.mocked(api.syncFavorites).mockResolvedValue({ success: true } as never);
    setup();
    await expandTestCollection();
    await act(async () => { calls[0].d.resolve({ success: true, total: 100, next_cursor: 'c2', items: [video({ id: 1, title: 'A page 1' })] }); });
    fireEvent.click(screen.getByText(en.nextPage));
    await act(async () => { calls[1].d.resolve({ success: true, total: 100, next_cursor: 'c3', items: [video({ id: 2, platform_item_id: 'b', title: 'B page 2' })] }); });
    const before = calls.length;

    fireEvent.click(screen.getByText(en.sync));
    await flush();

    expect(calls.length).toBeGreaterThan(before);
    expect(calls[calls.length - 1]).toMatchObject({ id: 'col-1', page: 2, cursor: 'c2' });
  });

  it('L3. clearing one collection does not refresh a different collection the user has moved to', async () => {
    vi.mocked(api.listCollections).mockResolvedValue({
      success: true, total: 2,
      items: [collection({ id: 1, collection_id: 'col-a', title: 'Collection A' }), collection({ id: 2, collection_id: 'col-b', title: 'Collection B' })],
    });
    const clear = deferred<{ success: boolean; reset_count: number }>();
    vi.mocked(api.clearAllKnowledge).mockReturnValue(clear.promise as never);
    setup();
    fireEvent.click(await screen.findByText('Collection A'));
    fireEvent.click(await screen.findByText(en.clearIngested)); // the clear is scoped to A
    fireEvent.click(screen.getByText('Collection B')); // the user moves on while it runs
    await flush();
    vi.mocked(api.listCollectionVideos).mockClear();

    await act(async () => { clear.resolve({ success: true, reset_count: 3 }); });

    expect(api.clearAllKnowledge).toHaveBeenCalledWith('col-a', 'douyin');
    expect(api.listCollectionVideos).not.toHaveBeenCalled();
  });

  it('L3b. clearing the collection the user is still on refreshes its first page, whatever page they were on', async () => {
    const calls = stubVideos();
    const clear = deferred<{ success: boolean; reset_count: number }>();
    vi.mocked(api.clearAllKnowledge).mockReturnValue(clear.promise as never);
    setup();
    await expandTestCollection();
    await act(async () => { calls[0].d.resolve({ success: true, total: 100, next_cursor: 'c2', items: [video({ id: 1, title: 'A page 1' })] }); });
    fireEvent.click(screen.getByText(en.nextPage));
    await act(async () => { calls[1].d.resolve({ success: true, total: 100, items: [video({ id: 2, platform_item_id: 'b', title: 'B page 2' })] }); });
    fireEvent.click(screen.getByText(en.clearIngested));
    const before = calls.length;

    await act(async () => { clear.resolve({ success: true, reset_count: 3 }); });

    expect(calls.length).toBeGreaterThan(before);
    expect(calls[calls.length - 1]).toMatchObject({ id: 'col-1', page: 1 });
  });

  it('L4. a page that fails to load keeps the rows already shown and ends the spinner', async () => {
    const calls = stubVideos();
    setup();
    await expandTestCollection();
    await act(async () => { calls[0].d.resolve({ success: true, total: 100, next_cursor: 'c2', items: [video({ id: 1, title: 'A page 1' })] }); });
    fireEvent.click(screen.getByText(en.nextPage));
    expect(screen.getByText(en.loadingVideos)).toBeTruthy();

    await act(async () => { calls[1].d.reject(new Error('offline')); });

    expect(screen.queryByText(en.loadingVideos)).toBeNull();
    expect(screen.getByText('A page 1')).toBeTruthy();
  });

  it('L4b. a first page that fails to load ends the spinner instead of spinning for ever', async () => {
    const calls = stubVideos();
    setup();
    await expandTestCollection();
    expect(screen.getByText(en.loadingVideos)).toBeTruthy();

    await act(async () => { calls[0].d.reject(new Error('offline')); });

    expect(screen.queryByText(en.loadingVideos)).toBeNull();
    expect(screen.getByText(en.noVideosPleaseSync)).toBeTruthy();
  });

  describe('with fake timers', () => {
    const advance = (ms: number) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });
    beforeEach(() => { vi.useFakeTimers(); });

    it('R1. the poll budget starts over for the platform the user switches to', async () => {
      vi.mocked(api.listCollections).mockResolvedValue({ success: true, items: [], total: 0 });
      render(<StoreConnectedPanel />);
      await advance(60_000);
      expect(api.listCollections).toHaveBeenCalledTimes(1 + 12); // the mount request + the 12-attempt budget
      await advance(30_000);
      expect(api.listCollections).toHaveBeenCalledTimes(1 + 12); // spent

      await act(async () => { useWorkspaceStore.getState().setSelectedPlatform('douyin'); });
      await advance(60_000);

      expect(api.listCollections).toHaveBeenCalledTimes(2 * (1 + 12)); // its own mount request and a full budget
    });
  });

  it('R2. a sync started on one platform refreshes the platform the user has moved to when it finishes', async () => {
    const sync = deferred<{ success: boolean }>();
    vi.mocked(api.syncFavorites).mockReturnValue(sync.promise as never);
    useWorkspaceStore.setState({ selectedPlatform: 'douyin' });
    render(<StoreConnectedPanel />);
    fireEvent.click(await screen.findByText(en.sync));
    expect(api.syncFavorites).toHaveBeenCalledWith('douyin');
    await act(async () => { useWorkspaceStore.getState().setSelectedPlatform('bilibili'); });
    vi.mocked(api.listCollections).mockClear();

    await act(async () => { sync.resolve({ success: true }); });

    const asked = vi.mocked(api.listCollections).mock.calls.map(call => call[0]);
    expect(asked.length).toBeGreaterThan(0);
    expect(asked.every(platform => platform === 'bilibili')).toBe(true);
  });
});
