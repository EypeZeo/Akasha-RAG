import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, act, fireEvent } from '@testing-library/react';
import SourcesStudio from './SourcesStudio';
import { I18nProvider, TRANSLATIONS } from '../i18n';
import * as api from '../api';
import { useWorkspaceStore } from '../store/workspace';

vi.mock('../api');

const rows = [
  { id: 1, collection_id: 'same', title: 'Douyin same', video_count: 1, is_active: true, platform: 'douyin' },
  { id: 2, collection_id: 'same', title: 'Bilibili same', video_count: 1, is_active: true, platform: 'bilibili' },
];

function renderStudio() {
  return render(
    <I18nProvider>
      <SourcesStudio
        onBuildDone={vi.fn()} statsRefreshKey={0} collectionsPerPage={20} videosPerPage={20} onOpenSettings={vi.fn()}
      />
    </I18nProvider>,
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
  useWorkspaceStore.setState({
    activeTab: 'sources', selectedCollectionId: 'all', selectedCollectionPlatform: null,
    selectedPlatform: 'all', activeSessionId: null,
  });
  vi.spyOn(window, 'confirm').mockReturnValue(true);
  vi.spyOn(window, 'alert').mockImplementation(() => {});
  vi.mocked(api.listCollections).mockResolvedValue({ success: true, items: rows, total: 2 });
  vi.mocked(api.getKnowledgeStats).mockResolvedValue({
    success: true, video_cache: { done: 2, pending: 0, failed: 0, downloading: 0, transcribing: 0 },
  });
  vi.mocked(api.clearAllKnowledge).mockResolvedValue({ success: true, reset_count: 1 });
});

describe('SourcesStudio connects the workspace store to SourcesPanel as one composite selection', () => {
  it('resolves the store\'s (id, owning platform) selection to exactly one row, so its actions are enabled and scoped to that platform', async () => {
    // The same remote id exists on both platforms; only the recorded owner says which one is selected.
    useWorkspaceStore.setState({ selectedCollectionId: 'same', selectedCollectionPlatform: 'bilibili' });
    renderStudio();
    await screen.findByText('Bilibili same');

    const clear = screen.getByText(TRANSLATIONS.en.clearIngested).closest('button') as HTMLButtonElement;
    expect(clear).not.toBeDisabled(); // ambiguous (disabled) if the owner never reached the panel

    await act(async () => { fireEvent.click(clear); });
    expect(api.clearAllKnowledge).toHaveBeenCalledWith('same', 'bilibili');
  });

  it('records the platform of a clicked row in the store, so the selection stays unambiguous afterwards', async () => {
    renderStudio();
    await screen.findByText('Bilibili same');

    fireEvent.click(screen.getByText('Bilibili same'));

    expect(useWorkspaceStore.getState()).toMatchObject({ selectedCollectionId: 'same', selectedCollectionPlatform: 'bilibili' });
  });
});
