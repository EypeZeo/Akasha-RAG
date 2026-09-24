import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import Workspace from './Workspace';
import { I18nProvider } from '../i18n';
import * as api from '../api';
import { useWorkspaceStore } from '../store/workspace';

vi.mock('../api');
// Only Workspace's own wiring is under test: the children that need a backend, a virtual list or
// a modal stack are replaced by stubs that expose the props Workspace hands them.
vi.mock('../components/ChatPanel', () => ({
  default: (props: { collectionId: string; platform: string }) => (
    <div data-testid="chat" data-collection={props.collectionId} data-platform={props.platform} />
  ),
}));
vi.mock('../components/SourcesStudio', () => ({ default: () => <div /> }));
vi.mock('../components/SettingsModal', () => ({ default: () => null }));
vi.mock('../components/LoginModal', () => ({ default: () => null }));
vi.mock('../components/ActivityBar', () => ({ default: () => null }));

function renderWorkspace() {
  return render(
    <I18nProvider>
      <Workspace onLogout={vi.fn()} theme={'light' as never} onThemeChange={vi.fn()} />
    </I18nProvider>,
  );
}

const chat = () => screen.getByTestId('chat');

beforeEach(() => {
  vi.restoreAllMocks();
  vi.stubGlobal('__APP_VERSION__', 'test');
  localStorage.clear();
  useWorkspaceStore.setState({
    activeTab: 'chat', selectedCollectionId: 'all', selectedCollectionPlatform: null,
    selectedPlatform: 'all', activeSessionId: null,
  });
  vi.mocked(api.listPlatforms).mockResolvedValue({ success: true, platforms: [] });
  vi.mocked(api.getLogLevel).mockResolvedValue({ success: false } as never);
  vi.mocked(api.getAudioCacheStats).mockResolvedValue({ success: false } as never);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('Workspace hands ChatPanel the platform its selected collection actually belongs to', () => {
  it('a real collection selected under one platform scopes chat to that platform, even while the browse filter says "all"', () => {
    useWorkspaceStore.setState({ selectedCollectionId: 'same', selectedCollectionPlatform: 'douyin', selectedPlatform: 'all' });
    renderWorkspace();
    expect(chat().dataset.collection).toBe('same');
    expect(chat().dataset.platform).toBe('douyin');
  });

  it('with no collection selected, chat follows the browse filter', () => {
    useWorkspaceStore.setState({ selectedCollectionId: 'all', selectedCollectionPlatform: null, selectedPlatform: 'bilibili' });
    renderWorkspace();
    expect(chat().dataset.platform).toBe('bilibili');
  });

  it('a real collection whose owner was never recorded also falls back to the browse filter', () => {
    useWorkspaceStore.setState({ selectedCollectionId: 'col-1', selectedCollectionPlatform: null, selectedPlatform: 'douyin' });
    renderWorkspace();
    expect(chat().dataset.platform).toBe('douyin');
  });
});
