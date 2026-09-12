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
  selectedPlatform: Platform;
  activeSessionId: number | null;

  setActiveTab: (tab: WorkspaceTab) => void;
  setSelectedCollectionId: (id: string) => void;
  setSelectedPlatform: (platform: Platform) => void;
  setActiveSessionId: (id: number | null) => void;

  /** Open a chat session and bring the chat tab forward in one step. */
  openSession: (id: number | null) => void;
}

export const useWorkspaceStore = create<WorkspaceState>((set) => ({
  activeTab: 'sources',
  selectedCollectionId: 'all',
  selectedPlatform: 'all',
  activeSessionId: null,

  setActiveTab: (activeTab) => set({ activeTab }),
  setSelectedCollectionId: (selectedCollectionId) => set({ selectedCollectionId }),
  setSelectedPlatform: (selectedPlatform) => set({ selectedPlatform }),
  setActiveSessionId: (activeSessionId) => set({ activeSessionId }),

  openSession: (activeSessionId) => set({ activeSessionId, activeTab: 'chat' }),
}));
