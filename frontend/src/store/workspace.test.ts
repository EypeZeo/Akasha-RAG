import { describe, it, expect, beforeEach } from 'vitest';
import { useWorkspaceStore } from './workspace';

const reset = () =>
  useWorkspaceStore.setState({
    activeTab: 'sources',
    selectedCollectionId: 'all',
    selectedPlatform: 'all',
    activeSessionId: null,
  });

describe('workspace store', () => {
  beforeEach(reset);

  it('openSession switches to the chat tab and sets the session in one step', () => {
    useWorkspaceStore.getState().openSession(42);
    expect(useWorkspaceStore.getState().activeSessionId).toBe(42);
    expect(useWorkspaceStore.getState().activeTab).toBe('chat');
  });

  it('changing the platform does not reset the selected collection or tab', () => {
    useWorkspaceStore.getState().setSelectedCollectionId('col-7');
    useWorkspaceStore.getState().setActiveTab('chat');
    useWorkspaceStore.getState().setSelectedPlatform('bilibili');
    expect(useWorkspaceStore.getState().selectedCollectionId).toBe('col-7');
    expect(useWorkspaceStore.getState().activeTab).toBe('chat');
    expect(useWorkspaceStore.getState().selectedPlatform).toBe('bilibili');
  });

  it('switching tabs preserves collection / platform / session selection', () => {
    const s = useWorkspaceStore.getState();
    s.setSelectedCollectionId('col-9');
    s.setSelectedPlatform('douyin');
    s.setActiveSessionId(5);
    s.setActiveTab('chat');
    s.setActiveTab('sources');
    const after = useWorkspaceStore.getState();
    expect(after).toMatchObject({
      selectedCollectionId: 'col-9',
      selectedPlatform: 'douyin',
      activeSessionId: 5,
      activeTab: 'sources',
    });
  });

  it('openSession(null) still forces the chat tab (new blank conversation)', () => {
    useWorkspaceStore.getState().openSession(null);
    expect(useWorkspaceStore.getState().activeSessionId).toBeNull();
    expect(useWorkspaceStore.getState().activeTab).toBe('chat');
  });
});
