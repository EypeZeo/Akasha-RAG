import { create } from 'zustand';

/**
 * Cross-panel workspace state.
 *
 * PR-B scope: the ephemeral state that both the Sources and Chat panes need to
 * agree on. Persisted UI preferences (activity-bar position, page sizes) stay in
 * `utils/settings` (`akasha:` localStorage keys). PR-C will migrate the chat
 * transcript / draft / streaming refs here so a tab switch never loses them.
 */
export type WorkspaceTab = 'sources' | 'chat';
export type Platform = 'all' | 'douyin' | 'bilibili';

interface WorkspaceState {
  activeTab: WorkspaceTab;
  selectedCollectionId: string;
  /** The platform the current `selectedCollectionId` was chosen under, or `null` for the
   *  synthetic 'all' row. A remote collection id is only unique per platform (Issue #27 /
   *  Issue #34), so a bare id is not enough to say which collection is actually selected. */
  selectedCollectionPlatform: string | null;
  selectedPlatform: Platform;
  activeSessionId: number | null;

  setActiveTab: (tab: WorkspaceTab) => void;
  setSelectedCollectionId: (id: string, platform?: string) => void;
  setSelectedPlatform: (platform: Platform) => void;
  setActiveSessionId: (id: number | null) => void;

  /** Open a chat session and bring the chat tab forward in one step. */
  openSession: (id: number | null) => void;
}

export const useWorkspaceStore = create<WorkspaceState>((set) => ({
  activeTab: 'sources',
  selectedCollectionId: 'all',
  selectedCollectionPlatform: null,
  selectedPlatform: 'all',
  activeSessionId: null,

  setActiveTab: (activeTab) => set({ activeTab }),
  setSelectedCollectionId: (selectedCollectionId, platform) => set(state => ({
    selectedCollectionId,
    // 'all' is a browse filter, not a platform a collection belongs to, so it is never recorded.
    selectedCollectionPlatform: selectedCollectionId === 'all'
      ? null
      : (platform ?? (state.selectedPlatform === 'all' ? null : state.selectedPlatform)),
  })),
  // A real collection selected under one platform stops meaning anything once the browse
  // filter moves to a different, specific platform that isn't the one it was chosen under —
  // keeping it would let a later action silently operate on the wrong platform's data.
  // Switching to 'all' does not clear it: 'all' still shows that collection's own row.
  setSelectedPlatform: (selectedPlatform) => set(state => ({
    selectedPlatform,
    ...(state.selectedCollectionId !== 'all' && selectedPlatform !== 'all' &&
      state.selectedCollectionPlatform !== selectedPlatform
      ? { selectedCollectionId: 'all', selectedCollectionPlatform: null }
      : {}),
  })),
  setActiveSessionId: (activeSessionId) => set({ activeSessionId }),

  openSession: (activeSessionId) => set({ activeSessionId, activeTab: 'chat' }),
}));
