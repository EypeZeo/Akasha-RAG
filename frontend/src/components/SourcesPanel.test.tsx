import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, act } from '@testing-library/react';
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

interface SetupProps {
  onBuildDone: () => void;
  selectedId: string;
  onSelectCollection: (id: string) => void;
  statsRefreshKey: number;
  collectionsPerPage: number;
  videosPerPage: number;
  onOpenSettings: () => void;
}

function setup(propsOverride: Partial<SetupProps> = {}) {
  const onBuildDone = propsOverride.onBuildDone ?? vi.fn();
  const onSelectCollection = propsOverride.onSelectCollection ?? vi.fn();
  const onOpenSettings = propsOverride.onOpenSettings ?? vi.fn();
  render(
    <I18nProvider>
      <SourcesPanel
        onBuildDone={onBuildDone}
        selectedId={propsOverride.selectedId ?? 'all'}
        onSelectCollection={onSelectCollection}
        statsRefreshKey={propsOverride.statsRefreshKey ?? 0}
        collectionsPerPage={propsOverride.collectionsPerPage ?? 20}
        videosPerPage={propsOverride.videosPerPage ?? 20}
        onOpenSettings={onOpenSettings}
      />
    </I18nProvider>,
  );
  return { onBuildDone, onSelectCollection, onOpenSettings };
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
