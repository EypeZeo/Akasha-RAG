import { describe, it, expect, beforeEach } from 'vitest';
import { useWorkspaceStore } from './workspace';

const reset = () =>
  useWorkspaceStore.setState({
    activeTab: 'sources',
    selectedCollectionId: 'all',
    selectedCollectionPlatform: null,
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

  it('selecting a real collection records the platform it was chosen under', () => {
    useWorkspaceStore.getState().setSelectedCollectionId('col-7', 'douyin');
    expect(useWorkspaceStore.getState()).toMatchObject({
      selectedCollectionId: 'col-7', selectedCollectionPlatform: 'douyin',
    });
  });

  it('selecting the synthetic "all" row clears the recorded platform, even if one is passed', () => {
    useWorkspaceStore.getState().setSelectedCollectionId('all', 'douyin');
    expect(useWorkspaceStore.getState()).toMatchObject({
      selectedCollectionId: 'all', selectedCollectionPlatform: null,
    });
  });

  it('omitting the platform falls back to the current browse filter when that is a specific platform', () => {
    useWorkspaceStore.getState().setSelectedPlatform('bilibili');
    useWorkspaceStore.getState().setSelectedCollectionId('col-1');
    expect(useWorkspaceStore.getState()).toMatchObject({
      selectedCollectionId: 'col-1', selectedCollectionPlatform: 'bilibili',
    });
  });

  it('omitting the platform while browsing "all" records no platform, never the string "all"', () => {
    // 'all' is a browse filter, not a platform a collection can belong to: storing it would
    // make the panel look for a row whose platform is literally 'all' and never resolve.
    useWorkspaceStore.getState().setSelectedCollectionId('col-1');
    expect(useWorkspaceStore.getState()).toMatchObject({
      selectedCollectionId: 'col-1', selectedCollectionPlatform: null,
    });
  });

  it('switching to a different, specific platform than the selected collection\'s own resets the composite selection', () => {
    useWorkspaceStore.getState().setSelectedCollectionId('col-7', 'douyin');
    useWorkspaceStore.getState().setActiveTab('chat');
    useWorkspaceStore.getState().setSelectedPlatform('bilibili');
    expect(useWorkspaceStore.getState().selectedCollectionId).toBe('all');
    expect(useWorkspaceStore.getState().selectedCollectionPlatform).toBeNull();
    expect(useWorkspaceStore.getState().activeTab).toBe('chat'); // unrelated state is untouched
    expect(useWorkspaceStore.getState().selectedPlatform).toBe('bilibili');
  });

  it('switching to the platform the collection is already owned by is a no-op for the selection', () => {
    useWorkspaceStore.getState().setSelectedCollectionId('col-7', 'douyin');
    useWorkspaceStore.getState().setSelectedPlatform('douyin');
    expect(useWorkspaceStore.getState()).toMatchObject({
      selectedCollectionId: 'col-7', selectedCollectionPlatform: 'douyin',
    });
  });

  it('switching to "all" preserves a real collection selection and its recorded platform', () => {
    useWorkspaceStore.getState().setSelectedCollectionId('same', 'douyin');
    useWorkspaceStore.getState().setSelectedPlatform('all');
    expect(useWorkspaceStore.getState()).toMatchObject({
      selectedCollectionId: 'same', selectedCollectionPlatform: 'douyin', selectedPlatform: 'all',
    });
  });

  it('no collection selected: switching platforms never touches selectedCollectionId/Platform', () => {
    useWorkspaceStore.getState().setSelectedPlatform('douyin');
    useWorkspaceStore.getState().setSelectedPlatform('bilibili');
    expect(useWorkspaceStore.getState()).toMatchObject({
      selectedCollectionId: 'all', selectedCollectionPlatform: null,
    });
  });

  it('switching tabs preserves collection / its platform / browse filter / session selection', () => {
    const s = useWorkspaceStore.getState();
    s.setSelectedPlatform('douyin');
    s.setSelectedCollectionId('col-9', 'douyin');
    s.setActiveSessionId(5);
    s.setActiveTab('chat');
    s.setActiveTab('sources');
    const after = useWorkspaceStore.getState();
    expect(after).toMatchObject({
      selectedCollectionId: 'col-9',
      selectedCollectionPlatform: 'douyin',
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
