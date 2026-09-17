import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, act, fireEvent } from '@testing-library/react';
import SourcesPanel from './SourcesPanel';
import { I18nProvider, TRANSLATIONS } from '../i18n';
import * as api from '../api';
import { useWorkspaceStore } from '../store/workspace';

vi.mock('../api');

const ACTIVE_EXPORT_KEY = 'akasha:active_export';

function makeCollection(overrides: Partial<api.CollectionItem> = {}): api.CollectionItem {
  return {
    id: 1,
    collection_id: 'col-1',
    title: 'Test Collection',
    video_count: 1,
    is_active: true,
    ...overrides,
  };
}

function makeVideo(overrides: Partial<api.VideoItem> = {}): api.VideoItem {
  return {
    id: 1,
    collection_id: 'col-1',
    platform_item_id: 'v1',
    url: 'https://www.douyin.com/video/v1',
    title: 'Test Video',
    author: 'Author',
    duration: 30,
    item_type: 'video',
    status: 'done',
    platform: 'douyin',
    ...overrides,
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(res => { resolve = res; });
  return { promise, resolve };
}

interface SetupProps {
  onBuildDone: () => void;
  selectedId: string;
  onSelectCollection: (id: string) => void;
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
        onSelectCollection={propsOverride.onSelectCollection ?? vi.fn()}
        statsRefreshKey={propsOverride.statsRefreshKey ?? 0}
        collectionsPerPage={propsOverride.collectionsPerPage ?? 20}
        videosPerPage={propsOverride.videosPerPage ?? 20}
        onOpenSettings={propsOverride.onOpenSettings ?? vi.fn()}
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
    onBuildDone,
    onSelectCollection,
    onOpenSettings,
    rerender: (nextOverride: Partial<SetupProps> = {}) =>
      rerender(buildElement({ ...resolvedProps, ...nextOverride })),
  };
}

/** Clicks the collection row identified by its title, toggling expand/collapse. */
function clickCollection(title: string) {
  act(() => {
    screen.getByText(title).click();
  });
}

beforeEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
  useWorkspaceStore.setState({
    activeTab: 'sources',
    selectedCollectionId: 'all',
    selectedPlatform: 'all',
    activeSessionId: null,
  });
  vi.spyOn(window, 'confirm').mockReturnValue(true);
  vi.spyOn(window, 'alert').mockImplementation(() => {});
  // 组件挂载时会调用 listCollections/getKnowledgeStats；baseline 用非空列表——
  // 若 listCollections 返回空数组，L128-154 的自愈轮询 effect 会在每个测试里都
  // 启动一个真实 setInterval(2000ms)，污染和它无关的测试的调用计数。只有专门
  // 测轮询本身的用例才会覆盖成空数组场景。
  vi.mocked(api.listCollections).mockResolvedValue({ success: true, items: [makeCollection()], total: 1 });
  vi.mocked(api.getKnowledgeStats).mockResolvedValue({ success: true });
});

afterEach(() => {
  vi.useRealTimers();
});

describe('SourcesPanel export status card', () => {
  it('does not render the raw backend exception text on export failure', async () => {
    // 模拟"刷新页面后恢复未完成的导出任务"这条路径：localStorage 里有一个
    // 活跃导出任务，组件挂载时会轮询一次 getExportProgress。
    localStorage.setItem(ACTIVE_EXPORT_KEY, JSON.stringify({ id: 'task-1', mode: 'local' }));
    const rawBackendMessage = 'PermissionError: [WinError 5] 拒绝访问: 导出目标目录';
    vi.mocked(api.getExportProgress).mockResolvedValue({
      success: true,
      status: 'failed',
      progress: 1,
      total: 1,
      message: rawBackendMessage,
    });
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});

    setup();

    await waitFor(() => {
      expect(api.getExportProgress).toHaveBeenCalled();
    });
    // Test environment defaults to English (no stored language preference,
    // navigator.language doesn't match a supported locale in jsdom).
    await waitFor(() => {
      expect(document.body.textContent).toContain(TRANSLATIONS.en.exportFailedRetry);
    });

    expect(document.body.textContent).not.toContain(rawBackendMessage);
    expect(errorSpy).toHaveBeenCalledWith('Export failed:', rawBackendMessage);
  });
});

describe('SourcesPanel collections list & pagination', () => {
  it('renders the collections returned by listCollections', async () => {
    vi.mocked(api.listCollections).mockResolvedValue({
      success: true, items: [makeCollection({ title: 'My Bilibili Favorites' })], total: 1,
    });

    setup();

    expect(await screen.findByText('My Bilibili Favorites')).toBeTruthy();
  });

  it('paginates real collections, with the "all" row pinned and excluded from paging', async () => {
    const allRow = makeCollection({ collection_id: 'all', title: '全部收藏', video_count: 5 });
    const real = [1, 2, 3, 4, 5].map(n => makeCollection({
      id: n, collection_id: `col-${n}`, title: `Collection ${n}`, video_count: n,
    }));
    vi.mocked(api.listCollections).mockResolvedValue({ success: true, items: [allRow, ...real], total: 6 });

    setup({ collectionsPerPage: 2 });

    // Page 1: pinned "All Favorites" + first 2 real collections.
    expect(await screen.findByText(TRANSLATIONS.en.allFavorites)).toBeTruthy();
    expect(screen.getByText('Collection 1')).toBeTruthy();
    expect(screen.getByText('Collection 2')).toBeTruthy();
    expect(screen.queryByText('Collection 3')).toBeNull();
    expect(screen.getByText(TRANSLATIONS.en.collectionPageInfo.replace('{current}', '1').replace('{total}', '3'))).toBeTruthy();
    expect(screen.getByText(TRANSLATIONS.en.prevPage).closest('button')).toHaveProperty('disabled', true);
    expect(screen.getByText(TRANSLATIONS.en.nextPage).closest('button')).toHaveProperty('disabled', false);

    act(() => {
      screen.getByText(TRANSLATIONS.en.nextPage).click();
    });

    expect(screen.getByText('Collection 3')).toBeTruthy();
    expect(screen.getByText('Collection 4')).toBeTruthy();
    expect(screen.queryByText('Collection 1')).toBeNull();

    act(() => {
      screen.getByText(TRANSLATIONS.en.nextPage).click();
    });

    expect(screen.getByText('Collection 5')).toBeTruthy();
    expect(screen.getByText(TRANSLATIONS.en.nextPage).closest('button')).toHaveProperty('disabled', true);
  });

  it('resets the collection page to 1 when the platform filter changes', async () => {
    const real = [1, 2, 3, 4, 5].map(n => makeCollection({
      id: n, collection_id: `col-${n}`, title: `Collection ${n}`, video_count: n,
    }));
    vi.mocked(api.listCollections).mockResolvedValue({ success: true, items: real, total: 5 });

    setup({ collectionsPerPage: 2 });

    await screen.findByText('Collection 1');
    act(() => { screen.getByText(TRANSLATIONS.en.nextPage).click(); });
    expect(screen.getByText('Collection 3')).toBeTruthy();

    act(() => {
      screen.getByText(TRANSLATIONS.en.platformBilibili).click();
    });

    await waitFor(() => {
      expect(screen.getByText('Collection 1')).toBeTruthy();
    });
    expect(screen.queryByText('Collection 3')).toBeNull();
  });

  it('keeps the selected collection visible as a pinned row even when its own page is not current', async () => {
    const real = [1, 2, 3, 4, 5].map(n => makeCollection({
      id: n, collection_id: `col-${n}`, title: `Collection ${n}`, video_count: n,
    }));
    vi.mocked(api.listCollections).mockResolvedValue({ success: true, items: real, total: 5 });

    // col-5 only lives on page 3 by default; selectedId pins it visible on page 1.
    setup({ collectionsPerPage: 2, selectedId: 'col-5' });

    expect(await screen.findByText('Collection 1')).toBeTruthy();
    expect(screen.getByText('Collection 5')).toBeTruthy();
  });

  it('stops polling once collections arrive, and does not poll again afterward', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.mocked(api.listCollections)
      .mockResolvedValueOnce({ success: true, items: [], total: 0 }) // mount
      .mockResolvedValueOnce({ success: true, items: [], total: 0 }) // poll tick 1
      .mockResolvedValue({ success: true, items: [makeCollection({ title: 'Recovered' })], total: 1 }); // poll tick 2+

    setup();
    await waitFor(() => expect(api.listCollections).toHaveBeenCalledTimes(1));
    const statsCallsAfterMount = vi.mocked(api.getKnowledgeStats).mock.calls.length;
    vi.mocked(api.listCollections).mockClear();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(4000); // 2 poll ticks
    });
    expect(api.listCollections).toHaveBeenCalledTimes(2);
    expect(screen.getByText('Recovered')).toBeTruthy();
    expect(vi.mocked(api.getKnowledgeStats).mock.calls.length).toBe(statsCallsAfterMount + 1);

    vi.mocked(api.listCollections).mockClear();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(20000); // interval genuinely cleared, not just hidden
    });
    expect(api.listCollections).not.toHaveBeenCalled();
  });

  it('gives up self-healing after 12 attempts and stops calling the API', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.mocked(api.listCollections).mockResolvedValue({ success: true, items: [], total: 0 });

    setup();
    await waitFor(() => expect(api.listCollections).toHaveBeenCalledTimes(1));
    vi.mocked(api.listCollections).mockClear();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(24000); // 12 poll ticks
    });
    expect(api.listCollections).toHaveBeenCalledTimes(12);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000); // 13th tick: gives up before calling the API
    });
    expect(api.listCollections).toHaveBeenCalledTimes(12);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(10000); // stays frozen, not just paused
    });
    expect(api.listCollections).toHaveBeenCalledTimes(12);
  });
});

describe('SourcesPanel expanded video list & search', () => {
  it('shows a loading state while videos are being fetched, then renders them', async () => {
    const { promise, resolve } = deferred<Awaited<ReturnType<typeof api.listCollectionVideos>>>();
    vi.mocked(api.listCollectionVideos).mockReturnValue(promise);

    setup();
    await screen.findByText('Test Collection');
    clickCollection('Test Collection');

    expect(await screen.findByText(TRANSLATIONS.en.loadingVideos)).toBeTruthy();

    await act(async () => {
      resolve({ success: true, items: [makeVideo({ title: 'Video A' })], total: 1 });
      await promise;
    });

    expect(await screen.findByText('Video A')).toBeTruthy();
    expect(screen.queryByText(TRANSLATIONS.en.loadingVideos)).toBeNull();
  });

  it('shows "no content yet" when a collection genuinely has no videos', async () => {
    vi.mocked(api.listCollectionVideos).mockResolvedValue({ success: true, items: [], total: 0 });

    setup();
    await screen.findByText('Test Collection');
    clickCollection('Test Collection');

    expect(await screen.findByText(TRANSLATIONS.en.noVideosPleaseSync)).toBeTruthy();
  });

  it('shows "no matches" (not "no content") when an active search filters every item out', async () => {
    vi.mocked(api.listCollectionVideos).mockResolvedValue({
      success: true,
      items: [makeVideo({ title: 'Alpha' }), makeVideo({ id: 2, platform_item_id: 'v2', title: 'Beta' })],
      total: 4, // >3 so the search box renders
    });

    setup();
    await screen.findByText('Test Collection');
    clickCollection('Test Collection');
    await screen.findByText('Alpha');

    vi.useFakeTimers({ shouldAdvanceTime: true });
    const search = screen.getByPlaceholderText(TRANSLATIONS.en.searchPlaceholder);
    fireEvent.change(search, { target: { value: 'nonexistent-zzz' } });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(300);
    });

    expect(await screen.findByText(TRANSLATIONS.en.noMatchedVideos)).toBeTruthy();
    expect(screen.queryByText(TRANSLATIONS.en.noVideosPleaseSync)).toBeNull();
  });

  it('filters the visible list by title after the 300ms search debounce, not before', async () => {
    vi.mocked(api.listCollectionVideos).mockResolvedValue({
      success: true,
      items: [makeVideo({ title: 'Alpha' }), makeVideo({ id: 2, platform_item_id: 'v2', title: 'Beta' })],
      total: 4,
    });

    setup();
    await screen.findByText('Test Collection');
    clickCollection('Test Collection');
    await screen.findByText('Alpha');
    expect(screen.getByText('Beta')).toBeTruthy();

    vi.useFakeTimers({ shouldAdvanceTime: true });
    const search = screen.getByPlaceholderText(TRANSLATIONS.en.searchPlaceholder);
    fireEvent.change(search, { target: { value: 'Alpha' } });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(299);
    });
    expect(screen.getByText('Beta')).toBeTruthy(); // debounce hasn't fired yet

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(screen.queryByText('Beta')).toBeNull(); // now filtered
    expect(screen.getByText('Alpha')).toBeTruthy();
  });

  it('shows the type-filter tabs whenever the collection has at least one note item — not only when mixed with video', async () => {
    vi.mocked(api.listCollectionVideos).mockResolvedValueOnce({
      success: true,
      items: [
        makeVideo({ title: 'A Video', item_type: 'video' }),
        makeVideo({ id: 2, platform_item_id: 'v2', title: 'A Note', item_type: 'note', duration: 0 }),
      ],
      total: 2,
      note_count: 1,
    });

    setup();
    await screen.findByText('Test Collection');
    clickCollection('Test Collection');
    await screen.findByText('A Video');

    // Real condition is `expandedVideos.length > 0 && hasNotesInCollection` (SourcesPanel.tsx L705) —
    // any note present shows the tabs, videos don't need to also be present. Lock that real behavior,
    // don't assume "mixed only".
    act(() => {
      screen.getByText(new RegExp(`^${TRANSLATIONS.en.imageNote}`)).click();
    });
    expect(screen.queryByText('A Video')).toBeNull();
    expect(screen.getByText('A Note')).toBeTruthy();
  });

  it('shows the type-filter tabs even for an all-notes collection with zero videos', async () => {
    vi.mocked(api.listCollectionVideos).mockResolvedValueOnce({
      success: true,
      items: [makeVideo({ title: 'Only A Note', item_type: 'note', duration: 0 })],
      total: 1,
      note_count: 1,
    });

    setup();
    await screen.findByText('Test Collection');
    clickCollection('Test Collection');
    await screen.findByText('Only A Note');

    // Scoped to a button (the type-filter tab), not the unrelated "Total N" stats
    // span in the Build & Clear section further down the same panel.
    expect(screen.getByRole('button', { name: new RegExp(`^${TRANSLATIONS.en.total}`) })).toBeTruthy();
  });

  it('hides the type-filter tabs for an all-video collection', async () => {
    vi.mocked(api.listCollectionVideos).mockResolvedValueOnce({
      success: true,
      items: [makeVideo({ title: 'Only A Video', item_type: 'video' })],
      total: 1,
      note_count: 0,
    });

    setup();
    await screen.findByText('Test Collection');
    clickCollection('Test Collection');
    await screen.findByText('Only A Video');

    expect(screen.queryByRole('button', { name: new RegExp(`^${TRANSLATIONS.en.total}`) })).toBeNull();
  });

  it('only shows the search box once the collection has more than 3 total items', async () => {
    vi.mocked(api.listCollectionVideos).mockResolvedValueOnce({
      success: true, items: [makeVideo({ title: 'V1' })], total: 3,
    });

    setup();
    await screen.findByText('Test Collection');
    clickCollection('Test Collection');
    await screen.findByText('V1');

    expect(screen.queryByPlaceholderText(TRANSLATIONS.en.searchPlaceholder)).toBeNull();
  });

  it('shows the search box once total exceeds 3', async () => {
    vi.mocked(api.listCollectionVideos).mockResolvedValueOnce({
      success: true, items: [makeVideo({ title: 'V1' })], total: 4,
    });

    setup();
    await screen.findByText('Test Collection');
    clickCollection('Test Collection');
    await screen.findByText('V1');

    expect(screen.getByPlaceholderText(TRANSLATIONS.en.searchPlaceholder)).toBeTruthy();
  });

  it('paginates videos, clearing any active search, and requests the right page from the API', async () => {
    vi.mocked(api.listCollectionVideos).mockResolvedValue({
      success: true, items: [makeVideo({ title: 'V1' })], total: 50, video_count: 50,
    });

    setup({ videosPerPage: 20 });
    await screen.findByText('Test Collection');
    clickCollection('Test Collection');
    await screen.findByText('V1');

    const search = screen.getByPlaceholderText(TRANSLATIONS.en.searchPlaceholder);
    fireEvent.change(search, { target: { value: 'foo' } });
    expect((search as HTMLInputElement).value).toBe('foo');

    vi.mocked(api.listCollectionVideos).mockClear();
    act(() => {
      screen.getByText(TRANSLATIONS.en.nextPage).click();
    });

    // platformFilter defaults to 'all' (see the shared beforeEach); fetchVideos falls back to it
    // when no explicit platform argument is passed, per SourcesPanel.tsx's own fetchVideos body.
    await waitFor(() => {
      expect(api.listCollectionVideos).toHaveBeenCalledWith('col-1', 2, 20, 'all', undefined);
    });
    expect((screen.getByPlaceholderText(TRANSLATIONS.en.searchPlaceholder) as HTMLInputElement).value).toBe('');
  });

  it('changing the page-size select clears search, resets to page 1, and refetches with the new size', async () => {
    vi.mocked(api.listCollectionVideos).mockResolvedValue({
      success: true, items: [makeVideo({ title: 'V1' })], total: 50,
    });

    setup();
    await screen.findByText('Test Collection');
    clickCollection('Test Collection');
    await screen.findByText('V1');

    const search = screen.getByPlaceholderText(TRANSLATIONS.en.searchPlaceholder);
    fireEvent.change(search, { target: { value: 'foo' } });

    vi.mocked(api.listCollectionVideos).mockClear();
    const select = screen.getByRole('combobox') as HTMLSelectElement;
    fireEvent.change(select, { target: { value: '50' } });

    await waitFor(() => {
      expect(api.listCollectionVideos).toHaveBeenCalledWith('col-1', 1, 50, 'all', undefined);
    });
    expect((screen.getByPlaceholderText(TRANSLATIONS.en.searchPlaceholder) as HTMLInputElement).value).toBe('');
  });

  it('re-fetches page 1 of the expanded collection when the videosPerPage prop changes', async () => {
    vi.mocked(api.listCollectionVideos).mockResolvedValue({
      success: true, items: [makeVideo({ title: 'V1' })], total: 1,
    });

    const { rerender } = setup({ videosPerPage: 20 });
    await screen.findByText('Test Collection');
    clickCollection('Test Collection');
    await screen.findByText('V1');

    vi.mocked(api.listCollectionVideos).mockClear();
    rerender({ videosPerPage: 50 });

    await waitFor(() => {
      expect(api.listCollectionVideos).toHaveBeenCalledWith('col-1', 1, 50, 'all', undefined);
    });
  });

  it('re-fetches the expanded collection videos when the platform filter changes', async () => {
    vi.mocked(api.listCollectionVideos).mockResolvedValue({
      success: true, items: [makeVideo({ title: 'V1' })], total: 1,
    });

    setup();
    await screen.findByText('Test Collection');
    clickCollection('Test Collection');
    await screen.findByText('V1');

    vi.mocked(api.listCollectionVideos).mockClear();
    act(() => {
      screen.getByText(TRANSLATIONS.en.platformBilibili).click();
    });

    // handlePlatformChange always resets to page 1 and passes the new platform explicitly.
    await waitFor(() => {
      expect(api.listCollectionVideos).toHaveBeenCalledWith('col-1', 1, 20, 'bilibili', undefined);
    });
  });

  it('collapses the collection on a second click without re-fetching', async () => {
    vi.mocked(api.listCollectionVideos).mockResolvedValue({
      success: true, items: [makeVideo({ title: 'V1' })], total: 1,
    });

    setup();
    await screen.findByText('Test Collection');
    clickCollection('Test Collection');
    await screen.findByText('V1');

    vi.mocked(api.listCollectionVideos).mockClear();
    clickCollection('Test Collection'); // collapse

    expect(screen.queryByText('V1')).toBeNull();
    expect(api.listCollectionVideos).not.toHaveBeenCalled();
  });
});

describe('SourcesPanel clear all knowledge', () => {
  function statsWithDone(done: number) {
    return { success: true, video_cache: { done, failed: 0, pending: 0, downloading: 0, transcribing: 0 } };
  }

  it('does nothing when the confirm dialog is cancelled', async () => {
    vi.mocked(api.getKnowledgeStats).mockResolvedValue(statsWithDone(3));
    vi.spyOn(window, 'confirm').mockReturnValue(false);

    setup();
    const clearBtn = await screen.findByText(TRANSLATIONS.en.clearIngested);
    act(() => { clearBtn.click(); });

    expect(api.clearAllKnowledge).not.toHaveBeenCalled();
  });

  it('clears everything and shows a localized success count when nothing is expanded', async () => {
    vi.mocked(api.getKnowledgeStats).mockResolvedValue(statsWithDone(3));
    vi.mocked(api.clearAllKnowledge).mockResolvedValue({ success: true, reset_count: 7 });
    const { onBuildDone } = setup();

    const clearBtn = await screen.findByText(TRANSLATIONS.en.clearIngested);
    act(() => { clearBtn.click(); });

    await waitFor(() => {
      expect(api.clearAllKnowledge).toHaveBeenCalledWith(undefined, 'all');
    });
    expect(window.alert).toHaveBeenCalledWith(TRANSLATIONS.en.clearKnowledgeSuccess.replace('{count}', '7'));
    expect(onBuildDone).toHaveBeenCalled();
  });

  it('scopes both the confirm wording and the API call to the expanded collection, then resets that collection to page 1', async () => {
    vi.mocked(api.getKnowledgeStats).mockResolvedValue(statsWithDone(3));
    vi.mocked(api.listCollectionVideos).mockResolvedValue({ success: true, items: [makeVideo({ title: 'V1' })], total: 1 });
    vi.mocked(api.clearAllKnowledge).mockResolvedValue({ success: true, reset_count: 2 });

    setup();
    await screen.findByText('Test Collection');
    clickCollection('Test Collection');
    await screen.findByText('V1');

    vi.mocked(api.listCollectionVideos).mockClear();
    const clearBtn = screen.getByText(TRANSLATIONS.en.clearIngested);
    act(() => { clearBtn.click(); });

    await waitFor(() => {
      expect(api.clearAllKnowledge).toHaveBeenCalledWith('col-1', 'all');
    });
    expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining(TRANSLATIONS.en.clearScopeCurrent));
    // fetchVideos(expandedId, 1, videoPageSize) — no explicit platform arg, so it falls
    // back to platformFilter ('all' by default, see the shared beforeEach).
    await waitFor(() => {
      expect(api.listCollectionVideos).toHaveBeenCalledWith('col-1', 1, 20, 'all', undefined);
    });
  });

  it('shows a generic failure alert without side effects when the backend reports success:false', async () => {
    vi.mocked(api.getKnowledgeStats).mockResolvedValue(statsWithDone(3));
    vi.mocked(api.clearAllKnowledge).mockResolvedValue({ success: false, reset_count: 0 });
    const { onBuildDone } = setup();

    const clearBtn = await screen.findByText(TRANSLATIONS.en.clearIngested);
    vi.mocked(api.getKnowledgeStats).mockClear();
    act(() => { clearBtn.click(); });

    await waitFor(() => {
      expect(api.clearAllKnowledge).toHaveBeenCalled();
    });
    expect(window.alert).toHaveBeenCalledWith(TRANSLATIONS.en.operationFailed);
    expect(api.getKnowledgeStats).not.toHaveBeenCalled();
    expect(onBuildDone).not.toHaveBeenCalled();
  });

  it('shows a generic failure alert and logs the raw error, never surfacing it in the UI, when the request throws', async () => {
    vi.mocked(api.getKnowledgeStats).mockResolvedValue(statsWithDone(3));
    const rawError = new Error('disk full: /var/data');
    vi.mocked(api.clearAllKnowledge).mockRejectedValue(rawError);
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});

    setup();
    const clearBtn = await screen.findByText(TRANSLATIONS.en.clearIngested);
    act(() => { clearBtn.click(); });

    await waitFor(() => {
      expect(errorSpy).toHaveBeenCalledWith('Clear ingested data failed:', rawError);
    });
    expect(window.alert).toHaveBeenCalledWith(TRANSLATIONS.en.operationFailed);
    expect(document.body.textContent).not.toContain('disk full');
  });

  it('disables the Clear Ingested button and shows "Cleaning..." while the request is in flight', async () => {
    vi.mocked(api.getKnowledgeStats).mockResolvedValue(statsWithDone(3));
    const { promise, resolve } = deferred<Awaited<ReturnType<typeof api.clearAllKnowledge>>>();
    vi.mocked(api.clearAllKnowledge).mockReturnValue(promise);

    setup();
    const clearBtn = await screen.findByText(TRANSLATIONS.en.clearIngested);
    act(() => { clearBtn.click(); });

    const cleaningLabel = await screen.findByText(TRANSLATIONS.en.cleaning);
    expect(cleaningLabel.closest('button')).toHaveProperty('disabled', true);

    await act(async () => {
      resolve({ success: true, reset_count: 1 });
      await promise;
    });

    expect(screen.queryByText(TRANSLATIONS.en.cleaning)).toBeNull();
  });
});

describe('SourcesPanel sync favorites', () => {
  function noopSync(overrides: Partial<Awaited<ReturnType<typeof api.syncFavorites>>> = {}) {
    return {
      success: true,
      added_videos: 0, removed_videos: 0, added_notes: 0, removed_notes: 0, invalid_count: 0,
      ...overrides,
    };
  }

  it('shows the "up to date" message and still refreshes collections and stats on a no-op sync', async () => {
    vi.mocked(api.syncFavorites).mockResolvedValue(noopSync());

    setup();
    const syncBtn = await screen.findByText(TRANSLATIONS.en.sync);
    await waitFor(() => expect(api.listCollections).toHaveBeenCalledTimes(1));
    vi.mocked(api.listCollections).mockClear();
    vi.mocked(api.getKnowledgeStats).mockClear();

    act(() => { syncBtn.click(); });

    await waitFor(() => {
      expect(window.alert).toHaveBeenCalledWith(TRANSLATIONS.en.syncUpToDate);
    });
    expect(api.listCollections).toHaveBeenCalledTimes(1);
    expect(api.getKnowledgeStats).toHaveBeenCalledTimes(1);
  });

  it('builds a combined added/removed videos+notes+invalid message for a representative mixed result', async () => {
    vi.mocked(api.syncFavorites).mockResolvedValue(noopSync({
      added_videos: 3, removed_videos: 1, added_notes: 2, invalid_count: 1,
    }));

    setup();
    const syncBtn = await screen.findByText(TRANSLATIONS.en.sync);
    act(() => { syncBtn.click(); });

    // Built by hand from the real i18n templates — aggregateSyncCounts' own math is
    // already covered by syncSummary.test.ts, this only proves handleSync's wiring.
    const expected = TRANSLATIONS.en.syncSuccessPrefix + [
      TRANSLATIONS.en.addAndRemoveVideos.replace('{add}', '3').replace('{remove}', '1'),
      TRANSLATIONS.en.addNotes.replace('{count}', '2'),
      TRANSLATIONS.en.syncInvalidCount.replace('{count}', '1'),
    ].join('，');

    await waitFor(() => {
      expect(window.alert).toHaveBeenCalledWith(expected);
    });
  });

  it('appends a per-platform failure suffix naming only the failed platform(s) even though sync overall succeeded', async () => {
    vi.mocked(api.syncFavorites).mockResolvedValue(noopSync({
      platform_results: { douyin: { success: true }, bilibili: { success: false } },
    }));

    setup();
    const syncBtn = await screen.findByText(TRANSLATIONS.en.sync);
    act(() => { syncBtn.click(); });

    const expected = TRANSLATIONS.en.syncUpToDate
      + TRANSLATIONS.en.syncPartialFailureSuffix.replace('{platforms}', TRANSLATIONS.en.platformBilibili);
    await waitFor(() => {
      expect(window.alert).toHaveBeenCalledWith(expected);
    });
  });

  it('fetches videos for the expanded collection after a successful sync, keeping the current page and explicit platform', async () => {
    vi.mocked(api.listCollectionVideos).mockResolvedValue({ success: true, items: [makeVideo({ title: 'V1' })], total: 1 });
    vi.mocked(api.syncFavorites).mockResolvedValue(noopSync());

    setup();
    await screen.findByText('Test Collection');
    clickCollection('Test Collection');
    await screen.findByText('V1');

    vi.mocked(api.listCollectionVideos).mockClear();
    const syncBtn = screen.getByText(TRANSLATIONS.en.sync);
    act(() => { syncBtn.click(); });

    // fetchVideos(expandedId, videoPage, videoPageSize, platformFilter) — unlike
    // clear-all, sync keeps the current page and passes platform explicitly.
    await waitFor(() => {
      expect(api.listCollectionVideos).toHaveBeenCalledWith('col-1', 1, 20, 'all', undefined);
    });
  });

  it('does not fetch videos after sync when no collection is expanded', async () => {
    vi.mocked(api.syncFavorites).mockResolvedValue(noopSync());

    setup();
    const syncBtn = await screen.findByText(TRANSLATIONS.en.sync);
    vi.mocked(api.listCollectionVideos).mockClear();
    act(() => { syncBtn.click(); });

    await waitFor(() => expect(api.syncFavorites).toHaveBeenCalled());
    expect(api.listCollectionVideos).not.toHaveBeenCalled();
  });

  it('shows a generic failure alert and logs the backend message, never surfacing it in the UI, when sync reports success:false', async () => {
    vi.mocked(api.syncFavorites).mockResolvedValue({ success: false, message: 'internal token expired' });
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});

    setup();
    const syncBtn = await screen.findByText(TRANSLATIONS.en.sync);
    act(() => { syncBtn.click(); });

    await waitFor(() => {
      expect(errorSpy).toHaveBeenCalledWith('Sync failed:', 'internal token expired');
    });
    expect(window.alert).toHaveBeenCalledWith(TRANSLATIONS.en.syncFailed);
    expect(document.body.textContent).not.toContain('internal token expired');
  });

  it('shows a generic failure alert and logs the thrown error when the sync request rejects', async () => {
    const rawError = new Error('network down');
    vi.mocked(api.syncFavorites).mockRejectedValue(rawError);
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});

    setup();
    const syncBtn = await screen.findByText(TRANSLATIONS.en.sync);
    act(() => { syncBtn.click(); });

    await waitFor(() => {
      expect(errorSpy).toHaveBeenCalledWith('Sync failed:', rawError);
    });
    expect(window.alert).toHaveBeenCalledWith(TRANSLATIONS.en.syncFailed);
  });

  it('disables the Sync button and shows the syncing state while the request is in flight', async () => {
    const { promise, resolve } = deferred<Awaited<ReturnType<typeof api.syncFavorites>>>();
    vi.mocked(api.syncFavorites).mockReturnValue(promise);

    setup();
    const syncBtn = await screen.findByText(TRANSLATIONS.en.sync);
    act(() => { syncBtn.click(); });

    const syncingLabel = await screen.findByText(TRANSLATIONS.en.syncing);
    expect(syncingLabel.closest('button')).toHaveProperty('disabled', true);

    await act(async () => {
      resolve(noopSync());
      await promise;
    });

    expect(screen.queryByText(TRANSLATIONS.en.syncing)).toBeNull();
  });
});

describe('SourcesPanel build submission (isSubmitting mutex + UI)', () => {
  it('20. two rapid retries on different failed videos only issue one syncKnowledge request', async () => {
    vi.mocked(api.listCollectionVideos).mockResolvedValue({
      success: true,
      items: [
        makeVideo({ id: 1, platform_item_id: 'fv1', title: 'Failed A', status: 'failed' }),
        makeVideo({ id: 2, platform_item_id: 'fv2', title: 'Failed B', status: 'failed' }),
      ],
      total: 2,
    });
    const { promise, resolve } = deferred<Awaited<ReturnType<typeof api.syncKnowledge>>>();
    vi.mocked(api.syncKnowledge).mockReturnValue(promise);
    vi.mocked(api.getSyncProgress).mockResolvedValue({ success: true, status: 'running', progress: 0, total: 1 });

    setup();
    await screen.findByText('Test Collection');
    clickCollection('Test Collection');
    await screen.findByText('Failed A');

    const retryButtons = screen.getAllByTitle(TRANSLATIONS.en.retryIngestTooltip);
    expect(retryButtons).toHaveLength(2);
    act(() => { retryButtons[0].click(); });
    act(() => { retryButtons[1].click(); }); // fired before syncKnowledge resolves

    expect(api.syncKnowledge).toHaveBeenCalledTimes(1);

    await act(async () => {
      resolve({ success: true, task_id: 'task-20', pending_count: 1 });
      await promise;
    });
  });

  it('21. isSubmitting is reflected in the UI: header text and the retry button are disabled before syncKnowledge resolves', async () => {
    vi.mocked(api.listCollectionVideos).mockResolvedValue({
      success: true,
      items: [makeVideo({ id: 1, platform_item_id: 'fv1', title: 'Failed A', status: 'failed' })],
      total: 1,
    });
    const { promise, resolve } = deferred<Awaited<ReturnType<typeof api.syncKnowledge>>>();
    vi.mocked(api.syncKnowledge).mockReturnValue(promise);
    vi.mocked(api.getSyncProgress).mockResolvedValue({ success: true, status: 'running', progress: 0, total: 1 });

    setup();
    await screen.findByText('Test Collection');
    clickCollection('Test Collection');
    await screen.findByText('Failed A');

    const retryButton = screen.getByTitle(TRANSLATIONS.en.retryIngestTooltip);
    act(() => { retryButton.click(); });

    expect(await screen.findByText(TRANSLATIONS.en.ingestSubmitting)).toBeTruthy();
    expect(retryButton).toHaveProperty('disabled', true);

    await act(async () => {
      resolve({ success: true, task_id: 'task-21', pending_count: 1 });
      await promise;
    });

    expect(screen.queryByText(TRANSLATIONS.en.ingestSubmitting)).toBeNull();
    // The header is "📥 {label}" — the emoji and label are separate text
    // nodes under the same <h3>, so an exact getByText(label) won't match
    // the node's full text; match on substring instead.
    await waitFor(() => {
      expect(screen.getByText((_, el) => el?.tagName === 'H3' && !!el.textContent?.includes(TRANSLATIONS.en.ingesting))).toBeTruthy();
    });
    expect(retryButton).toHaveProperty('disabled', true); // now disabled via `building`, not `isSubmitting`
  });
});
