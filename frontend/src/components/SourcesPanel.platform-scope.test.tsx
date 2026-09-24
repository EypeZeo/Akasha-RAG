/**
 * Issue #27: SourcesPanel's collection-page reset can swallow a fast click (AC1/AC2),
 * and collections/videos requests have no protection against a stale response arriving
 * after a newer one (AC3). Comments in #24/#25 attributed the original flake to other
 * causes without proof (AC4 — corrected below, not repeated here).
 *
 * This file is deliberately separate from SourcesPanel.test.tsx: it is the full
 * red-then-green record for Issue #27, kept out of the (already large) main suite.
 */
import { Profiler } from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, act, fireEvent } from '@testing-library/react';
import SourcesPanel from './SourcesPanel';
import { I18nProvider, TRANSLATIONS } from '../i18n';
import * as api from '../api';
import { useWorkspaceStore } from '../store/workspace';

vi.mock('../api');

function makeCollection(overrides: Partial<api.CollectionItem> = {}): api.CollectionItem {
  return {
    id: 1, collection_id: 'col-1', title: 'Test Collection', video_count: 1, is_active: true,
    platform: 'douyin', ...overrides,
  };
}

function makeVideo(overrides: Partial<api.VideoItem> = {}): api.VideoItem {
  return {
    id: 1, collection_id: 'col-1', platform_item_id: 'v1', url: 'https://www.douyin.com/video/v1',
    title: 'Test Video', author: 'Author', duration: 30, item_type: 'video', status: 'done',
    platform: 'douyin', ...overrides,
  };
}

function realCollections(n: number): api.CollectionItem[] {
  return Array.from({ length: n }, (_, i) => makeCollection({
    id: i + 1, collection_id: `col-${i + 1}`, title: `Collection ${i + 1}`,
  }));
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

const fullStats = (overrides: Record<string, unknown> = {}) => ({
  success: true,
  video_cache: { done: 3, pending: 3, failed: 0, downloading: 0, transcribing: 0 },
  detail: { video: { total: 3, done: 2, pending: 2 }, note: { total: 3, done: 1, pending: 1 } },
  ...overrides,
});

interface SetupProps {
  onBuildDone: () => void;
  selectedId: string;
  selectedOwner?: string;
  onSelectCollection: (id: string, owner?: string) => void;
  statsRefreshKey: number;
  collectionsPerPage: number;
  videosPerPage: number;
  onOpenSettings: () => void;
}

function buildElement(propsOverride: Partial<SetupProps>) {
  return (
    <I18nProvider>
      <SourcesPanel
        onBuildDone={propsOverride.onBuildDone ?? vi.fn()}
        selectedId={propsOverride.selectedId ?? 'all'}
        selectedOwner={propsOverride.selectedOwner}
        onSelectCollection={propsOverride.onSelectCollection ?? vi.fn()}
        statsRefreshKey={propsOverride.statsRefreshKey ?? 0}
        collectionsPerPage={propsOverride.collectionsPerPage ?? 20}
        videosPerPage={propsOverride.videosPerPage ?? 20}
        onOpenSettings={propsOverride.onOpenSettings ?? vi.fn()}
        {...(propsOverride as object)}
      />
    </I18nProvider>
  );
}

function setup(propsOverride: Partial<SetupProps> = {}) {
  const onBuildDone = propsOverride.onBuildDone ?? vi.fn();
  const onSelectCollection = propsOverride.onSelectCollection ?? vi.fn();
  const onOpenSettings = propsOverride.onOpenSettings ?? vi.fn();
  const resolvedProps = { ...propsOverride, onBuildDone, onSelectCollection, onOpenSettings };
  const { rerender } = render(buildElement(resolvedProps));
  return {
    onBuildDone, onSelectCollection, onOpenSettings,
    rerender: (nextOverride: Partial<SetupProps> = {}) => rerender(buildElement({ ...resolvedProps, ...nextOverride })),
  };
}

/** Mounts through a wrapper that subscribes to the store exactly like SourcesStudio does,
 *  so a store-level platform switch genuinely cascades into SourcesPanel's props — a static
 *  prop passed directly to SourcesPanel cannot exercise that cascade at all. */
function StoreConnectedPanel(props: Partial<{ onBuildDone: () => void; statsRefreshKey: number; collectionsPerPage: number; videosPerPage: number; onOpenSettings: () => void }> = {}) {
  const selectedCollectionId = useWorkspaceStore(s => s.selectedCollectionId);
  const selectedCollectionPlatform = useWorkspaceStore(s => s.selectedCollectionPlatform);
  const setSelectedCollectionId = useWorkspaceStore(s => s.setSelectedCollectionId);
  return (
    <I18nProvider>
      <SourcesPanel
        onBuildDone={props.onBuildDone ?? vi.fn()}
        selectedId={selectedCollectionId}
        selectedOwner={selectedCollectionPlatform ?? undefined}
        onSelectCollection={setSelectedCollectionId}
        statsRefreshKey={props.statsRefreshKey ?? 0}
        collectionsPerPage={props.collectionsPerPage ?? 20}
        videosPerPage={props.videosPerPage ?? 20}
        onOpenSettings={props.onOpenSettings ?? vi.fn()}
      />
    </I18nProvider>
  );
}

function renderStoreConnected(props: Parameters<typeof StoreConnectedPanel>[0] = {}) {
  return render(<StoreConnectedPanel {...props} />);
}

const pageInfo = (current: number, total: number) =>
  TRANSLATIONS.en.collectionPageInfo.replace('{current}', String(current)).replace('{total}', String(total));

beforeEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
  useWorkspaceStore.setState({
    activeTab: 'sources', selectedCollectionId: 'all', selectedCollectionPlatform: null,
    selectedPlatform: 'all', activeSessionId: null,
  });
  vi.spyOn(window, 'confirm').mockReturnValue(true);
  vi.spyOn(window, 'alert').mockImplementation(() => {});
  vi.mocked(api.listCollections).mockResolvedValue({ success: true, items: [makeCollection()], total: 1 });
  vi.mocked(api.getKnowledgeStats).mockResolvedValue({ success: true });
  vi.mocked(api.listCollectionVideos).mockResolvedValue({ success: true, items: [], total: 0 });
});

afterEach(() => {
  vi.useRealTimers();
});

// =====================================================================================
// P1 — AC1: a click landing between the list's commit and the passive-effect flush
// =====================================================================================

describe('P1 — the collection-page reset cannot swallow a click that lands right after the list commits (AC1)', () => {
  /** How this puts the click inside the window: IS_REACT_ACT_ENVIRONMENT=false stops Testing
   *  Library from flushing effects for us, so React schedules the commit's passive effects as its
   *  own later task, as it does in a browser. A MutationObserver callback runs as a microtask right
   *  after the DOM mutation, so resuming from `await committed` and clicking immediately runs before
   *  that later task — an ordering that comes from microtasks draining before the next task, not
   *  from winning a race against a timer. This claims only that the ordering is stable on this
   *  project's locked React / jsdom / Node versions (the stability loop in the PR checks it), not
   *  that it is a guarantee of any other runtime or scheduler. */
  async function clickNextAfterListCommit({ flushEffectsFirst }: { flushEffectsFirst: boolean }) {
    const list = deferred<{ success: boolean; items: api.CollectionItem[]; total: number }>();
    vi.mocked(api.listCollections).mockReturnValue(list.promise);
    setup({ collectionsPerPage: 2 });

    const actEnv = globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean };
    actEnv.IS_REACT_ACT_ENVIRONMENT = false;
    try {
      const committed = new Promise<void>(resolve => {
        const check = () => { if (document.body.textContent?.includes('Collection 1')) { observer.disconnect(); resolve(); } };
        const observer = new MutationObserver(check);
        observer.observe(document.body, { subtree: true, childList: true, characterData: true });
        check();
      });
      list.resolve({ success: true, items: realCollections(5), total: 5 });
      await committed;
      if (flushEffectsFirst) await new Promise(r => setTimeout(r, 30));
      screen.getByText(TRANSLATIONS.en.nextPage).click(); // a plain DOM click, like a user's
      await new Promise(r => setTimeout(r, 60));
    } finally {
      actEnv.IS_REACT_ACT_ENVIRONMENT = true;
    }
  }

  it('P1a. reaches page 2 even when the click lands before any passive effect has flushed', async () => {
    await clickNextAfterListCommit({ flushEffectsFirst: false });
    expect(screen.getByText('Collection 3')).toBeTruthy();
    expect(screen.getByText(pageInfo(2, 3))).toBeTruthy();
  });

  it('P1b. control: the same click after effects have already flushed also reaches page 2', async () => {
    await clickNextAfterListCommit({ flushEffectsFirst: true });
    expect(screen.getByText('Collection 3')).toBeTruthy();
  });
});

// =====================================================================================
// P2 — AC2: the page resets only on the three named explicit-intent triggers
// =====================================================================================

describe('P2 — the collection page resets only on platform change, per-page change, or an explicit refresh (AC2)', () => {
  // Every test here waits for the list's passive effects to flush before its first "Next" click, so
  // none of them depends on the AC1 window (that is P1's job alone) and each tests exactly one thing.
  const settle = () => act(async () => { await new Promise(r => setTimeout(r, 50)); });
  const goToPage2 = async () => {
    await screen.findByText('Collection 1');
    await settle();
    fireEvent.click(screen.getByText(TRANSLATIONS.en.nextPage));
    await screen.findByText('Collection 3');
  };

  it('P2a. changing the platform resets the page to 1', async () => {
    vi.mocked(api.listCollections).mockResolvedValue({ success: true, items: realCollections(5), total: 5 });
    setup({ collectionsPerPage: 2 });
    await goToPage2();

    await act(async () => { useWorkspaceStore.getState().setSelectedPlatform('bilibili'); });
    await settle();

    expect(screen.getByText(pageInfo(1, 3))).toBeTruthy();
    expect(screen.queryByText('Collection 3')).toBeNull();
  });

  it('P2a2. going A→B→A resets on the way back too: the old page is not revived', async () => {
    vi.mocked(api.listCollections).mockResolvedValue({ success: true, items: realCollections(5), total: 5 });
    setup({ collectionsPerPage: 2 });
    await goToPage2(); // page 2 of 'all'

    await act(async () => { useWorkspaceStore.getState().setSelectedPlatform('bilibili'); });
    await settle();
    await act(async () => { useWorkspaceStore.getState().setSelectedPlatform('all'); }); // back, without ever paging on bilibili
    await settle();

    expect(screen.getByText(pageInfo(1, 3))).toBeTruthy();
    expect(screen.queryByText('Collection 3')).toBeNull();
  });

  it('P2b. changing the per-page setting resets the page to 1', async () => {
    vi.mocked(api.listCollections).mockResolvedValue({ success: true, items: realCollections(5), total: 5 });
    const h = setup({ collectionsPerPage: 2 });
    await goToPage2();

    h.rerender({ collectionsPerPage: 3 });
    await settle();

    expect(screen.getByText(pageInfo(1, 2))).toBeTruthy();
    expect(screen.getByText('Collection 1')).toBeTruthy();
  });

  it('P2c. clicking the already-selected platform (an explicit refresh) resets the page to 1', async () => {
    vi.mocked(api.listCollections).mockResolvedValue({ success: true, items: realCollections(5), total: 5 });
    setup({ collectionsPerPage: 2 });
    await goToPage2();
    vi.mocked(api.listCollections).mockClear();

    fireEvent.click(screen.getByText(TRANSLATIONS.en.platformAll)); // already selected — manual refresh
    await settle();

    expect(api.listCollections).toHaveBeenCalled();
    expect(screen.getByText(pageInfo(1, 3))).toBeTruthy();
    expect(screen.getByText('Collection 1')).toBeTruthy();
  });

  it('P2d. the list shrinking clamps the page to the last valid one instead of resetting to 1', async () => {
    vi.mocked(api.listCollections).mockResolvedValue({ success: true, items: realCollections(5), total: 5 });
    const h = setup({ collectionsPerPage: 2 });
    await screen.findByText('Collection 1');
    await settle();
    fireEvent.click(screen.getByText(TRANSLATIONS.en.nextPage));
    fireEvent.click(screen.getByText(TRANSLATIONS.en.nextPage));
    await screen.findByText('Collection 5'); // page 3 of 3

    // The parent asks for a refresh (e.g. after an ingest) and the list comes back shorter: 3 items, 2 pages.
    vi.mocked(api.listCollections).mockResolvedValue({ success: true, items: realCollections(3), total: 3 });
    h.rerender({ statsRefreshKey: 1 });
    await settle();

    // Page 3 no longer exists, so the view lands on page 2 — the last valid one — not on page 1.
    expect(screen.getByText(pageInfo(2, 2))).toBeTruthy();
    expect(screen.getByText('Collection 3')).toBeTruthy();
    expect(screen.queryByText('Collection 1')).toBeNull();
  });

  it('P2e. the list growing while on page 2 keeps the user on page 2', async () => {
    vi.mocked(api.listCollections).mockResolvedValue({ success: true, items: realCollections(5), total: 5 });
    const h = setup({ collectionsPerPage: 2 });
    await goToPage2(); // page 2 of 3

    vi.mocked(api.listCollections).mockResolvedValue({ success: true, items: realCollections(7), total: 7 });
    h.rerender({ statsRefreshKey: 1 });
    await settle();

    expect(screen.getByText(pageInfo(2, 4))).toBeTruthy();
    expect(screen.getByText('Collection 3')).toBeTruthy();
  });
});

// =====================================================================================
// P3 — AC3: a stale collections/videos response cannot overwrite a newer one
// =====================================================================================

describe('P3 — a stale response cannot overwrite a newer filter\'s result (AC3)', () => {
  it('P3a. collections: an old platform\'s response arriving after a newer one is ignored', async () => {
    const allReq = deferred<{ success: boolean; items: api.CollectionItem[]; total: number }>();
    const biliReq = deferred<{ success: boolean; items: api.CollectionItem[]; total: number }>();
    vi.mocked(api.listCollections).mockImplementation(plat => (plat === 'bilibili' ? biliReq.promise : allReq.promise));
    setup();

    await act(async () => { useWorkspaceStore.getState().setSelectedPlatform('bilibili'); });
    await act(async () => {
      biliReq.resolve({ success: true, items: [makeCollection({ id: 7, collection_id: 'b-1', title: 'Bilibili Only', platform: 'bilibili' })], total: 1 });
    });
    expect(await screen.findByText('Bilibili Only')).toBeTruthy();

    // The request issued before the switch (for the OLD platform) answers last.
    await act(async () => {
      allReq.resolve({ success: true, items: [makeCollection({ id: 8, collection_id: 'a-1', title: 'All Platforms Item' })], total: 1 });
    });
    expect(screen.queryByText('All Platforms Item')).toBeNull();
    expect(screen.getByText('Bilibili Only')).toBeTruthy();
  });

  it('P3b. videos: an old collection\'s response arriving after a newer collection was expanded is ignored', async () => {
    vi.mocked(api.listCollections).mockResolvedValue({
      success: true, total: 2,
      items: [makeCollection({ id: 1, collection_id: 'col-x', title: 'Collection X' }), makeCollection({ id: 2, collection_id: 'col-y', title: 'Collection Y' })],
    });
    const xVideos = deferred<{ success: boolean; items: api.VideoItem[]; total: number }>();
    const yVideos = deferred<{ success: boolean; items: api.VideoItem[]; total: number }>();
    vi.mocked(api.listCollectionVideos).mockImplementation(id => (id === 'col-x' ? xVideos.promise : yVideos.promise));
    setup();
    await screen.findByText('Collection X');

    fireEvent.click(screen.getByText('Collection X'));
    fireEvent.click(screen.getByText('Collection X')); // collapse X
    fireEvent.click(screen.getByText('Collection Y')); // expand Y instead
    await act(async () => {
      yVideos.resolve({ success: true, items: [makeVideo({ platform_item_id: 'y1', title: 'Y video' })], total: 1 });
    });
    expect(await screen.findByText('Y video')).toBeTruthy();

    await act(async () => {
      xVideos.resolve({ success: true, items: [makeVideo({ platform_item_id: 'x1', title: 'X video' })], total: 1 });
    });
    expect(screen.queryByText('X video')).toBeNull();
    expect(screen.getByText('Y video')).toBeTruthy();
  });

  it('P3c. videos: switching platform while viewing the synthetic "all" row hides the old platform\'s videos at once, then shows the new platform\'s', async () => {
    // Only the synthetic "all favorites" row has no owning platform, so only its videos depend on the view.
    const allRow = makeCollection({ id: 0, collection_id: 'all', title: '全部收藏', platform: undefined });
    vi.mocked(api.listCollections).mockResolvedValue({ success: true, items: [allRow], total: 1 });
    const biliVideos = deferred<{ success: boolean; items: api.VideoItem[]; total: number }>();
    vi.mocked(api.listCollectionVideos).mockImplementation(async (_id, _page, _size, plat) => {
      if (plat === 'bilibili') return biliVideos.promise;
      return { success: true, items: [makeVideo({ title: 'All-platform video' })], total: 1 };
    });
    setup();
    await screen.findByText(TRANSLATIONS.en.allFavorites);
    fireEvent.click(screen.getByText(TRANSLATIONS.en.allFavorites));
    await screen.findByText('All-platform video');

    await act(async () => { useWorkspaceStore.getState().setSelectedPlatform('bilibili'); });
    expect(screen.queryByText('All-platform video')).toBeNull(); // hidden immediately, not just eventually

    await act(async () => { biliVideos.resolve({ success: true, items: [makeVideo({ title: 'Bilibili video' })], total: 1 }); });
    expect(await screen.findByText('Bilibili video')).toBeTruthy();
    expect(screen.queryByText('All-platform video')).toBeNull();
  });

  it('P3e. videos: not one commit after a platform switch paints the previous platform\'s videos, not even the first', async () => {
    // Same setup as P3c. `act` also runs the platform-change effect before it returns, and that effect's
    // loading state already hides the old videos by the time P3c looks — so only a probe that records
    // the page at every commit can see the very first commit, which is the one the scope check protects.
    const allRow = makeCollection({ id: 0, collection_id: 'all', title: '全部收藏', platform: undefined });
    vi.mocked(api.listCollections).mockResolvedValue({ success: true, items: [allRow], total: 1 });
    const biliVideos = deferred<{ success: boolean; items: api.VideoItem[]; total: number }>();
    vi.mocked(api.listCollectionVideos).mockImplementation(async (_id, _page, _size, plat) => {
      if (plat === 'bilibili') return biliVideos.promise;
      return { success: true, items: [makeVideo({ title: 'All-platform video' })], total: 1 };
    });
    const commits: string[] = [];
    render(<Profiler id="commit-probe" onRender={() => commits.push(document.body.textContent ?? '')}>{buildElement({})}</Profiler>);
    await screen.findByText(TRANSLATIONS.en.allFavorites);
    fireEvent.click(screen.getByText(TRANSLATIONS.en.allFavorites));
    await screen.findByText('All-platform video');

    commits.length = 0;
    await act(async () => { useWorkspaceStore.getState().setSelectedPlatform('bilibili'); });

    expect(commits.length).toBeGreaterThan(0);
    expect(commits.some(text => text.includes('All-platform video'))).toBe(false);
  });

  it('P3f. videos: returning to a platform never paints the videos the other platform loaded in the meantime', async () => {
    // The collections gate hides every row (and the videos inside the expanded one) after a switch, so
    // the scope check on the shown videos only matters when the list on screen is current again while a
    // *different* platform's videos are what sits in state: switch away, let that platform's videos land
    // while its collections list is still on its way, then come back to the platform whose list is cached.
    const allRow = makeCollection({ id: 0, collection_id: 'all', title: '全部收藏', platform: undefined });
    vi.mocked(api.listCollections).mockImplementation(plat => (plat === 'bilibili'
      ? new Promise<never>(() => {}) // bilibili's list never arrives
      : Promise.resolve({ success: true, items: [allRow], total: 1 })));
    vi.mocked(api.listCollectionVideos).mockImplementation(async (_id, _page, _size, plat) => ({
      success: true, total: 1, items: [makeVideo({ title: plat === 'bilibili' ? 'Bilibili-only video' : 'All-platform video' })],
    }));
    const commits: string[] = [];
    render(<Profiler id="commit-probe" onRender={() => commits.push(document.body.textContent ?? '')}>{buildElement({})}</Profiler>);
    await screen.findByText(TRANSLATIONS.en.allFavorites);
    fireEvent.click(screen.getByText(TRANSLATIONS.en.allFavorites));
    await screen.findByText('All-platform video');

    await act(async () => { useWorkspaceStore.getState().setSelectedPlatform('bilibili'); });
    await act(async () => { await new Promise(r => setTimeout(r, 50)); }); // bilibili's videos are now in state, hidden

    commits.length = 0;
    await act(async () => { useWorkspaceStore.getState().setSelectedPlatform('all'); });

    expect(commits.length).toBeGreaterThan(0);
    expect(commits.some(text => text.includes('Bilibili-only video'))).toBe(false);
    expect(await screen.findByText('All-platform video')).toBeTruthy(); // and the right ones come back
  });

  it('P3d. collections: switching platform hides the old platform\'s rows at once and shows a loading state until the new list arrives', async () => {
    useWorkspaceStore.setState({ selectedPlatform: 'douyin' });
    const pendingBili = deferred<{ success: boolean; items: api.CollectionItem[]; total: number }>();
    vi.mocked(api.listCollections).mockImplementation(plat => (plat === 'bilibili'
      ? pendingBili.promise
      : Promise.resolve({ success: true, total: 1, items: [makeCollection({ collection_id: 'd1', title: 'Douyin old row', platform: 'douyin' })] })));
    setup();
    await screen.findByText('Douyin old row');

    await act(async () => { useWorkspaceStore.getState().setSelectedPlatform('bilibili'); });
    // Gone in the very first commit after the switch — not merely once the new list has replaced it.
    expect(screen.queryByText('Douyin old row')).toBeNull();
    expect(screen.getByLabelText(TRANSLATIONS.en.loadingVideos)).toHaveAttribute('aria-busy', 'true');

    await act(async () => {
      pendingBili.resolve({ success: true, total: 1, items: [makeCollection({ collection_id: 'b1', title: 'Bilibili new row', platform: 'bilibili' })] });
    });
    expect(await screen.findByText('Bilibili new row')).toBeTruthy();
    expect(screen.queryByLabelText(TRANSLATIONS.en.loadingVideos)).toBeNull();
  });
});

// =====================================================================================
// P4 — platform-scoped actions: an incompatible/ambiguous selection cannot be acted on
// =====================================================================================

describe('P4 — actions are scoped to a real, unambiguous, platform-compatible target', () => {
  const sameOnTwoPlatforms = () => vi.mocked(api.listCollections).mockImplementation(async plat => ({
    success: true, total: plat === 'all' || !plat ? 2 : 1,
    items: plat === 'douyin' ? [makeCollection({ id: 1, collection_id: 'same', title: 'Douyin same', platform: 'douyin' })]
      : plat === 'bilibili' ? [makeCollection({ id: 2, collection_id: 'same', title: 'Bilibili same', platform: 'bilibili' })]
      : [makeCollection({ id: 1, collection_id: 'same', title: 'Douyin same', platform: 'douyin' }), makeCollection({ id: 2, collection_id: 'same', title: 'Bilibili same', platform: 'bilibili' })],
  }));

  it('P4.1. switching to an incompatible platform resets the store\'s composite selection', async () => {
    sameOnTwoPlatforms();
    renderStoreConnected();
    await screen.findByText('Douyin same');
    fireEvent.click(screen.getByText('Douyin same')); // selects + expands the douyin row
    await waitFor(() => expect(useWorkspaceStore.getState().selectedCollectionId).toBe('same'));
    expect(useWorkspaceStore.getState().selectedCollectionPlatform).toBe('douyin');

    // (the platform tab, not the "Bilibili" badge on rows — both carry the same text)
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: TRANSLATIONS.en.platformBilibili })); });

    expect(useWorkspaceStore.getState().selectedCollectionId).toBe('all');
    expect(useWorkspaceStore.getState().selectedCollectionPlatform).toBeNull();
  });

  /** Every place an action can start from: the header Export, the bottom Batch Export, Clear all,
   *  Ingest, and the "only video" / "only note" shortcuts. */
  const actionEntries = () => ({
    headerExport: screen.getByTitle(TRANSLATIONS.en.batchExportTooltip) as HTMLButtonElement,
    bottomExport: screen.getByText(new RegExp(TRANSLATIONS.en.batchExport)).closest('button') as HTMLButtonElement,
    clear: screen.getByText(TRANSLATIONS.en.clearIngested).closest('button') as HTMLButtonElement,
    ingest: screen.getByText(new RegExp(TRANSLATIONS.en.oneClickIngest)).closest('button') as HTMLButtonElement,
    quickVideo: screen.getByText(new RegExp(TRANSLATIONS.en.onlyIngestVideo)).closest('button') as HTMLButtonElement,
    quickNote: screen.getByText(new RegExp(TRANSLATIONS.en.onlyIngestNote)).closest('button') as HTMLButtonElement,
  });

  it('P4.2. all six action entries render, enabled, when there is real, unambiguous work to do', async () => {
    vi.mocked(api.getKnowledgeStats).mockResolvedValue(fullStats());
    setup();
    await screen.findByText('Test Collection');
    const entries = Object.values(actionEntries());
    expect(entries).toHaveLength(6);
    for (const button of entries) expect(button).not.toBeDisabled();
  });

  it('P4.3. an ambiguous selection (no owner, id exists on two platforms) disables every action entry and blocks their side effects', async () => {
    sameOnTwoPlatforms();
    vi.mocked(api.getKnowledgeStats).mockResolvedValue(fullStats());
    setup({ selectedId: 'same' }); // no selectedOwner — ambiguous among the two 'same' rows
    await screen.findByText('Douyin same');

    const entries = Object.values(actionEntries());
    for (const button of entries) expect(button).toBeDisabled();

    for (const button of entries) fireEvent.click(button);
    expect(window.confirm).not.toHaveBeenCalled();
    expect(api.clearAllKnowledge).not.toHaveBeenCalled();
    expect(api.syncKnowledge).not.toHaveBeenCalled();
    expect(api.getSettingsStatus).not.toHaveBeenCalled(); // neither ingest entry got as far as checking readiness
    expect(screen.queryByRole('dialog')).toBeNull(); // and no export/build modal opened
  });

  it('P4.4. a failed collections load (no list to act on) disables every action entry and says so', async () => {
    vi.mocked(api.listCollections).mockResolvedValue({ success: false } as unknown as { success: boolean; items: api.CollectionItem[]; total: number });
    vi.mocked(api.getKnowledgeStats).mockResolvedValue(fullStats());
    setup();

    expect(await screen.findByRole('alert')).toBeTruthy();
    for (const button of Object.values(actionEntries())) expect(button).toBeDisabled();
  });

  it('P4.5. in the "all" view, clearing a real row sends that row\'s own platform, never "all"', async () => {
    vi.mocked(api.listCollections).mockResolvedValue({ success: true, items: [makeCollection({ collection_id: 'b1', title: 'Bili row', platform: 'bilibili' })], total: 1 });
    vi.mocked(api.getKnowledgeStats).mockResolvedValue(fullStats());
    vi.mocked(api.clearAllKnowledge).mockResolvedValue({ success: true, reset_count: 1 });
    setup();
    await screen.findByText('Bili row');
    fireEvent.click(screen.getByText('Bili row')); // expand it — platformFilter is still 'all'

    await act(async () => { fireEvent.click(screen.getByText(TRANSLATIONS.en.clearIngested)); });

    expect(api.clearAllKnowledge).toHaveBeenCalledWith('b1', 'bilibili');
  });

  it('P4.10. exporting from a real row carries that row\'s platform through the modal into the export request', async () => {
    vi.mocked(api.listCollections).mockResolvedValue({ success: true, items: [makeCollection({ collection_id: 'b1', title: 'Bili row', platform: 'bilibili' })], total: 1 });
    vi.mocked(api.getKnowledgeStats).mockResolvedValue(fullStats());
    vi.mocked(api.exportBatchStart).mockResolvedValue({ success: true, task_id: 'task', mode: 'browser' });
    vi.mocked(api.getExportProgress).mockResolvedValue({ success: true, status: 'running', progress: 0 } as never);
    setup(); // the "all" view — the request platform must come from the row, not from the view
    await screen.findByText('Bili row');
    fireEvent.click(screen.getByText('Bili row'));

    fireEvent.click(screen.getByText(new RegExp(TRANSLATIONS.en.batchExport)));
    fireEvent.click(await screen.findByText(TRANSLATIONS.en.destBrowser));
    fireEvent.click(screen.getByText(TRANSLATIONS.en.startExportBrowser));

    await waitFor(() => expect(api.exportBatchStart).toHaveBeenCalledWith(expect.objectContaining({
      collection_id: 'b1', platform: 'bilibili',
    })));
  });

  it('P4.6. A→B→A: without re-expanding, the row stays collapsed and issues no automatic video request; only a manual re-expand fetches fresh data', async () => {
    // Distinct ids on purpose: this test is about the round trip, not about id collisions (P4.7/P4.8).
    const douyinRow = makeCollection({ id: 1, collection_id: 'col-d', title: 'Douyin row', platform: 'douyin' });
    const biliRow = makeCollection({ id: 2, collection_id: 'col-b', title: 'Bilibili row', platform: 'bilibili' });
    vi.mocked(api.listCollections).mockImplementation(async plat => ({
      success: true, total: 1, items: plat === 'bilibili' ? [biliRow] : [douyinRow],
    }));
    vi.mocked(api.listCollectionVideos).mockResolvedValueOnce({ success: true, items: [makeVideo({ title: 'Old video' })], total: 1 });
    useWorkspaceStore.setState({ selectedPlatform: 'douyin' });
    setup();
    await screen.findByText('Douyin row');
    fireEvent.click(screen.getByText('Douyin row'));
    await screen.findByText('Old video');
    vi.mocked(api.listCollectionVideos).mockClear();

    await act(async () => { useWorkspaceStore.getState().setSelectedPlatform('bilibili'); });
    await screen.findByText('Bilibili row');
    await act(async () => { useWorkspaceStore.getState().setSelectedPlatform('douyin'); });
    await screen.findByText('Douyin row');

    // Collapsed, not silently revived with the pre-round-trip video — and no request was
    // ever made for it on the way back, since nothing is expanded to request videos for.
    expect(screen.queryByText('Old video')).toBeNull();
    expect(api.listCollectionVideos).not.toHaveBeenCalled();

    vi.mocked(api.listCollectionVideos).mockResolvedValueOnce({ success: true, items: [makeVideo({ title: 'Fresh video' })], total: 1 });
    fireEvent.click(screen.getByText('Douyin row')); // manual re-expand
    expect(await screen.findByText('Fresh video')).toBeTruthy();
    expect(api.listCollectionVideos).toHaveBeenCalledTimes(1);
  });

  it('P4.7. two rows sharing one remote id: expanding one expands only that row, and its videos are requested for its own platform', async () => {
    sameOnTwoPlatforms();
    setup(); // the "all" view lists both
    await screen.findByText('Douyin same');

    fireEvent.click(screen.getByText('Douyin same'));

    await waitFor(() => expect(api.listCollectionVideos).toHaveBeenCalled());
    expect(api.listCollectionVideos).toHaveBeenCalledWith('same', 1, 20, 'douyin', undefined);
    expect(screen.getAllByText('▲')).toHaveLength(1); // exactly one row carries the expanded marker
  });

  it('P4.8. same remote id: switching to the other platform collapses the expansion and never shows its same-id row as expanded', async () => {
    sameOnTwoPlatforms();
    setup();
    await screen.findByText('Douyin same');
    fireEvent.click(screen.getByText('Douyin same'));
    await waitFor(() => expect(api.listCollectionVideos).toHaveBeenCalledTimes(1));
    vi.mocked(api.listCollectionVideos).mockClear();

    await act(async () => { useWorkspaceStore.getState().setSelectedPlatform('bilibili'); });
    await screen.findByText('Bilibili same');

    expect(api.listCollectionVideos).not.toHaveBeenCalled(); // nothing requested for the old platform's id
    expect(screen.queryAllByText('▲')).toHaveLength(0); // the Bilibili row with the same remote id is not shown expanded

    fireEvent.click(screen.getByText('Bilibili same'));
    await waitFor(() => expect(api.listCollectionVideos).toHaveBeenCalledWith('same', 1, 20, 'bilibili', undefined));
  });

  it('P4.9. a real row stays expanded, without reloading, when the view narrows to its own platform', async () => {
    // Distinct ids: the same-id collision is P4.7/P4.8's job; this is only about not reloading needlessly.
    const douyinRow = makeCollection({ id: 1, collection_id: 'col-d', title: 'Douyin row', platform: 'douyin' });
    const biliRow = makeCollection({ id: 2, collection_id: 'col-b', title: 'Bilibili row', platform: 'bilibili' });
    vi.mocked(api.listCollections).mockImplementation(async plat => ({
      success: true, total: 2, items: plat === 'douyin' ? [douyinRow] : plat === 'bilibili' ? [biliRow] : [douyinRow, biliRow],
    }));
    vi.mocked(api.listCollectionVideos).mockResolvedValue({ success: true, items: [makeVideo({ title: 'Kept video' })], total: 1 });
    setup(); // the "all" view lists both rows
    await screen.findByText('Douyin row');
    fireEvent.click(screen.getByText('Douyin row'));
    await screen.findByText('Kept video');
    vi.mocked(api.listCollectionVideos).mockClear();

    await act(async () => { useWorkspaceStore.getState().setSelectedPlatform('douyin'); });
    await screen.findByText('Douyin row');

    expect(screen.getByText('Kept video')).toBeTruthy();
    expect(api.listCollectionVideos).not.toHaveBeenCalled(); // still valid for its own platform: no needless reload
  });
});

// =====================================================================================
// P5 — a same-platform refresh failure keeps the stale list operable, with a clear signal
// =====================================================================================

describe('P5 — a refresh failure on the current platform is shown, scoped, and clears correctly', () => {
  it('P5a. a manual refresh that fails on the current platform shows a stale-data notice and keeps the list operable', async () => {
    vi.mocked(api.listCollections).mockResolvedValueOnce({ success: true, items: [makeCollection()], total: 1 });
    setup();
    await screen.findByText('Test Collection');

    vi.mocked(api.listCollections).mockRejectedValueOnce(new Error('network'));
    await act(async () => { fireEvent.click(screen.getByText(TRANSLATIONS.en.platformAll)); });

    expect(screen.getByText('Test Collection')).toBeTruthy(); // stale list stays visible and unchanged
    expect(screen.getByRole('alert')).toBeTruthy();
  });

  it('P5b. a subsequent successful refresh on the same platform clears the notice', async () => {
    vi.mocked(api.listCollections).mockResolvedValueOnce({ success: true, items: [makeCollection()], total: 1 });
    setup();
    await screen.findByText('Test Collection');
    vi.mocked(api.listCollections).mockRejectedValueOnce(new Error('network'));
    await act(async () => { fireEvent.click(screen.getByText(TRANSLATIONS.en.platformAll)); });
    expect(screen.getByRole('alert')).toBeTruthy();

    vi.mocked(api.listCollections).mockResolvedValueOnce({ success: true, items: [makeCollection()], total: 1 });
    await act(async () => { fireEvent.click(screen.getByText(TRANSLATIONS.en.platformAll)); });

    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('P5c. a failure on platform A does not show its notice after switching to platform B', async () => {
    vi.mocked(api.listCollections).mockResolvedValueOnce({ success: true, items: [makeCollection()], total: 1 });
    setup();
    await screen.findByText('Test Collection');
    vi.mocked(api.listCollections).mockRejectedValueOnce(new Error('network'));
    await act(async () => { fireEvent.click(screen.getByText(TRANSLATIONS.en.platformAll)); });
    expect(screen.getByRole('alert')).toBeTruthy();

    const pendingBili = deferred<{ success: boolean; items: api.CollectionItem[]; total: number }>();
    vi.mocked(api.listCollections).mockReturnValue(pendingBili.promise);
    await act(async () => { useWorkspaceStore.getState().setSelectedPlatform('bilibili'); });
    // Right after the switch, while bilibili's own list is still on its way: A's notice must not linger.
    expect(screen.queryByRole('alert')).toBeNull();

    await act(async () => {
      pendingBili.resolve({ success: true, items: [makeCollection({ collection_id: 'b1', platform: 'bilibili', title: 'Bili row' })], total: 1 });
    });
    expect(await screen.findByText('Bili row')).toBeTruthy();
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('P5d. a refresh that is aborted because a newer one replaced it does not raise the notice', async () => {
    vi.mocked(api.listCollections).mockResolvedValueOnce({ success: true, items: [makeCollection()], total: 1 });
    setup();
    await screen.findByText('Test Collection');

    // First refresh: stays pending until it is aborted, then rejects like a real fetch would.
    vi.mocked(api.listCollections).mockImplementationOnce((_plat, signal) => new Promise((_resolve, reject) => {
      signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')));
    }));
    fireEvent.click(screen.getByText(TRANSLATIONS.en.platformAll));
    // Second refresh replaces it, and stays pending for now.
    const second = deferred<{ success: boolean; items: api.CollectionItem[]; total: number }>();
    vi.mocked(api.listCollections).mockReturnValueOnce(second.promise);
    await act(async () => { fireEvent.click(screen.getByText(TRANSLATIONS.en.platformAll)); });
    await act(async () => {}); // the first refresh's abort rejection has now been delivered

    // Between the aborted request failing and its replacement answering, nothing is reported: the
    // final state alone would hide a notice raised in this window, because the success clears it.
    expect(screen.queryByRole('alert')).toBeNull();

    await act(async () => {
      second.resolve({ success: true, items: [makeCollection({ title: 'Second refresh' })], total: 1 });
    });
    expect(await screen.findByText('Second refresh')).toBeTruthy();
    expect(screen.queryByRole('alert')).toBeNull();
  });
});

// =====================================================================================
// Polling regressions (section 4.2): the empty-list self-heal poll must not race,
// duplicate or starve itself
// =====================================================================================

describe('the empty-list poll does not race, duplicate or starve itself (Issue #27 follow-on)', () => {
  type ListResult = { success: boolean; items: api.CollectionItem[]; total: number };
  const empty: ListResult = { success: true, items: [], total: 0 };
  // Fake timers are active in this block: assertions use getByText/queryByText after an explicit
  // flush, never findByText or waitFor (their internal polling runs on the real clock and would hang).
  const advance = (ms: number) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });

  beforeEach(() => { vi.useFakeTimers(); });

  it('4.2.1. a mount-time request still in flight suppresses the first poll tick (no duplicate request), and its late result is accepted', async () => {
    const mountReq = deferred<ListResult>();
    vi.mocked(api.listCollections)
      .mockReturnValueOnce(mountReq.promise)
      .mockResolvedValue({ success: true, items: [makeCollection({ title: 'Duplicate poll result' })], total: 1 });
    setup();
    await advance(2500); // past the first 2000ms poll tick

    expect(api.listCollections).toHaveBeenCalledTimes(1); // the poll did not issue a second, overlapping request
    expect(screen.queryByText('Duplicate poll result')).toBeNull();

    await act(async () => {
      mountReq.resolve({ success: true, items: [makeCollection({ title: 'Arrived late' })], total: 1 });
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByText('Arrived late')).toBeTruthy();
    expect(screen.queryByText('Duplicate poll result')).toBeNull();
  });

  it('4.2.2. a hung poll request is never overlapped by further ticks; once its deadline passes the next tick issues and succeeds', async () => {
    const hung = deferred<ListResult>(); // ignores the abort signal, like a request that never answers
    vi.mocked(api.listCollections)
      .mockResolvedValueOnce(empty) // mount: empty, so the poll starts
      .mockReturnValueOnce(hung.promise) // attempt 1 (t=2000): hangs
      .mockResolvedValueOnce({ success: true, items: [makeCollection({ title: 'Recovered' })], total: 1 }); // attempt 2
    setup();
    await advance(2000); // attempt 1 issued
    expect(api.listCollections).toHaveBeenCalledTimes(2);

    // The ticks at t=4000/6000/8000 land while attempt 1 is still in flight (its deadline is 7000ms
    // after it was issued, i.e. t=9000). Keep this literal in sync with COLLECTIONS_REQUEST_DEADLINE_MS.
    await advance(6900); // t=8900
    expect(api.listCollections).toHaveBeenCalledTimes(2); // none of those ticks issued an overlapping request
    expect(screen.queryByText('Recovered')).toBeNull();

    await advance(1200); // t=10100: attempt 1 timed out at t=9000, the t=10000 tick is a fresh attempt
    expect(api.listCollections).toHaveBeenCalledTimes(3);
    expect(screen.getByText('Recovered')).toBeTruthy();
  });

  it('4.2.3. skipped ticks do not consume attempts: after a hung first attempt there are still exactly 12 real poll attempts', async () => {
    const hung = deferred<ListResult>();
    vi.mocked(api.listCollections)
      .mockResolvedValueOnce(empty) // mount
      .mockReturnValueOnce(hung.promise) // attempt 1 hangs until its deadline
      .mockResolvedValue(empty); // every later attempt answers, still empty
    setup();
    await advance(60_000);
    expect(api.listCollections).toHaveBeenCalledTimes(1 + 12); // the mount request + exactly 12 poll attempts, then it stops
    await advance(30_000);
    expect(api.listCollections).toHaveBeenCalledTimes(1 + 12); // and stays stopped
  });

  it('4.2.4. switching platform aborts the old platform\'s request, and its hang neither blocks nor overwrites the new platform', async () => {
    const hungA = deferred<ListResult>();
    let biliCalls = 0;
    vi.mocked(api.listCollections).mockImplementation(async plat => {
      if (plat === 'douyin') return hungA.promise;
      if (plat === 'bilibili') {
        return biliCalls++ === 0 ? empty : { success: true, items: [makeCollection({ title: 'B healed', platform: 'bilibili' })], total: 1 };
      }
      return empty;
    });
    renderStoreConnected();
    await advance(0);
    await act(async () => { useWorkspaceStore.getState().setSelectedPlatform('douyin'); });
    await advance(0); // A's request is now in flight and never answers
    await act(async () => { useWorkspaceStore.getState().setSelectedPlatform('bilibili'); });
    await advance(0); // B: its own mount request answers empty, so B's poll starts

    const douyinSignal = vi.mocked(api.listCollections).mock.calls.find(([plat]) => plat === 'douyin')?.[1];
    expect(douyinSignal?.aborted).toBe(true);

    await advance(2000); // B's first poll tick — not suppressed by A's stuck request
    expect(screen.getByText('B healed')).toBeTruthy();

    await act(async () => {
      hungA.resolve({ success: true, items: [makeCollection({ title: 'A stale' })], total: 1 });
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.queryByText('A stale')).toBeNull();
    expect(screen.getByText('B healed')).toBeTruthy();
  });

  it('4.2.5. a manual refresh aborts the in-flight poll; the aborted poll settling late neither clears the manual request\'s claim nor overwrites it', async () => {
    const pollReq = deferred<ListResult>();
    const manualReq = deferred<ListResult>();
    vi.mocked(api.listCollections)
      .mockResolvedValueOnce(empty) // mount
      .mockReturnValueOnce(pollReq.promise) // poll attempt (t=2000), left hanging
      .mockReturnValueOnce(manualReq.promise); // the manual refresh, also pending for a while
    setup();
    await advance(2000);
    const pollSignal = vi.mocked(api.listCollections).mock.calls[1][1];

    await act(async () => {
      fireEvent.click(screen.getByText(TRANSLATIONS.en.platformAll)); // already selected: a manual refresh
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(pollSignal?.aborted).toBe(true);
    expect(api.listCollections).toHaveBeenCalledTimes(3);

    // The aborted poll finally settles, like a real fetch rejecting with AbortError...
    await act(async () => { pollReq.reject(new DOMException('aborted', 'AbortError')); await vi.advanceTimersByTimeAsync(0); });
    // ...and must not have cleared the slot the manual request now owns: this tick must not duplicate it.
    await advance(2000);
    expect(api.listCollections).toHaveBeenCalledTimes(3);
    expect(screen.queryByRole('alert')).toBeNull(); // an aborted request is not a failure to report

    await act(async () => {
      manualReq.resolve({ success: true, items: [makeCollection({ title: 'Manual refresh result' })], total: 1 });
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByText('Manual refresh result')).toBeTruthy();
  });

  it('4.2.6. a first request that never answers ends in a visible failure once its deadline passes, not an endless skeleton', async () => {
    vi.mocked(api.listCollections)
      .mockReturnValueOnce(deferred<ListResult>().promise) // never answers
      .mockResolvedValue(empty);
    setup();
    expect(screen.getByLabelText(TRANSLATIONS.en.loadingVideos)).toHaveAttribute('aria-busy', 'true'); // waiting

    await advance(7100); // past the 7000ms deadline, before the first retry tick at t=8000
    expect(screen.getByRole('alert')).toBeTruthy();
    expect(screen.queryByLabelText(TRANSLATIONS.en.loadingVideos)).toBeNull();
  });

  it('4.2.7. a refresh that never answers marks the list it is refreshing as possibly stale once its deadline passes', async () => {
    vi.mocked(api.listCollections).mockResolvedValueOnce({ success: true, items: [makeCollection()], total: 1 });
    setup();
    await advance(0);
    expect(screen.getByText('Test Collection')).toBeTruthy();

    vi.mocked(api.listCollections).mockReturnValueOnce(deferred<ListResult>().promise); // the refresh never answers
    await act(async () => {
      fireEvent.click(screen.getByText(TRANSLATIONS.en.platformAll));
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.queryByRole('alert')).toBeNull(); // still within its deadline

    await advance(7100);
    expect(screen.getByRole('alert')).toBeTruthy();
    expect(screen.getByText('Test Collection')).toBeTruthy(); // the list itself stays, and stays usable
  });

  it('4.2.8. unmounting aborts the in-flight request and stops the poll', async () => {
    vi.mocked(api.listCollections)
      .mockReturnValueOnce(deferred<ListResult>().promise)
      .mockResolvedValue(empty);
    const view = render(buildElement({}));
    await advance(0);
    const signal = vi.mocked(api.listCollections).mock.calls[0][1];

    view.unmount();

    expect(signal?.aborted).toBe(true);
    const callsAtUnmount = vi.mocked(api.listCollections).mock.calls.length;
    await advance(30_000);
    expect(vi.mocked(api.listCollections).mock.calls.length).toBe(callsAtUnmount);
  });
});
