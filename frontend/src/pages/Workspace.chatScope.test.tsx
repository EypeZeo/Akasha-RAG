/**
 * Issue #40: in Chat, the platform chips are the *browse filter*, while a chat request carries the *effective* scope
 * (the selected collection's own platform whenever a collection is selected). The two can differ in exactly one
 * state: a collection is selected and the browse filter says "All". The chips keep meaning "browse filter" (clicking
 * "All" stays a browse-only action); the label next to them says what is actually searched.
 *
 * These tests run the real Workspace with the real ChatPanel and drive the chips and the composer like a user, then
 * assert three things per case: which chip is pressed, what the scope label says, and what chatAskStream was called
 * with. The invariant behind every case: while a collection is selected the label's platform is the request's
 * platform; without one, the pressed chip is.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, act, fireEvent } from '@testing-library/react';
import Workspace from './Workspace';
import { I18nProvider, TRANSLATIONS } from '../i18n';
import * as api from '../api';
import { useWorkspaceStore } from '../store/workspace';

vi.mock('../api');
vi.mock('@tanstack/react-virtual', async () => {
  const { createVirtualizerModuleMock } = await import('../test/virtualizerFake');
  return createVirtualizerModuleMock();
});
// Only the scope wiring is under test: the parts that need a backend or a modal stack are stubbed.
vi.mock('../components/ChatMessageRow', () => ({
  default: ({ msg }: { msg: { content: string } }) => <div data-testid="msg-row">{msg.content}</div>,
}));
vi.mock('../components/SourcesStudio', () => ({ default: () => <div /> }));
vi.mock('../components/SettingsModal', () => ({ default: () => null }));
vi.mock('../components/LoginModal', () => ({ default: () => null }));
vi.mock('../components/ActivityBar', () => ({ default: () => null }));

const en = TRANSLATIONS.en;
type ChipName = 'All' | 'Douyin' | 'Bilibili';
const CHIPS: ChipName[] = ['All', 'Douyin', 'Bilibili'];

function renderWorkspace() {
  return render(
    <I18nProvider>
      <Workspace onLogout={vi.fn()} theme={'light' as never} onThemeChange={vi.fn()} />
    </I18nProvider>,
  );
}

const chip = (name: ChipName) => screen.getByRole('button', { name: name === 'All' ? new RegExp(`^(🌐 )?${en.scopeAll}$`) : name });
const pressedChips = () => CHIPS.filter(name => chip(name).getAttribute('aria-pressed') === 'true');
const clickChip = async (name: ChipName) => { await act(async () => { fireEvent.click(chip(name)); }); };
/** The scope label (only shown while a collection is selected): its text, or null when it is not on screen. */
const scopeLabel = () => screen.queryByText(new RegExp(en.searchCollectionOnly))?.textContent?.replace(/\s+/g, ' ').trim() ?? null;
const labelText = (platform: string) => `${en.searchCollectionOnly} · ${platform}`;

/** Sends a question through the composer and returns what chatAskStream was called with: [collection_id, platform]. */
async function ask() {
  const box = await screen.findByPlaceholderText(en.inputPlaceholder);
  fireEvent.change(box, { target: { value: 'what is this about?' } });
  await act(async () => { fireEvent.keyDown(box, { key: 'Enter' }); });
  const calls = vi.mocked(api.chatAskStream).mock.calls;
  const call = calls[calls.length - 1];
  expect(call, 'chatAskStream was not called').toBeTruthy();
  return { collectionId: call![2], platform: call![3] };
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.stubGlobal('__APP_VERSION__', 'test');
  localStorage.clear();
  useWorkspaceStore.setState({
    activeTab: 'chat', selectedCollectionId: 'all', selectedCollectionPlatform: null, selectedPlatform: 'all', activeSessionId: null,
  });
  vi.mocked(api.listPlatforms).mockResolvedValue({ success: true, platforms: [] });
  vi.mocked(api.getLogLevel).mockResolvedValue({ success: false } as never);
  vi.mocked(api.getAudioCacheStats).mockResolvedValue({ success: false } as never);
  vi.mocked(api.getKnowledgeStats).mockResolvedValue({ success: true, video_cache: { done: 0 }, detail: { note: { done: 0 } } });
  vi.mocked(api.listSessions).mockResolvedValue({ success: true, items: [] });
  vi.mocked(api.getSettingsStatus).mockResolvedValue({ success: true, chat_ready: true, ingest_ready: true });
  vi.mocked(api.chatAskStream).mockImplementation((() => (async function* () { /* the reply is not under test */ })()) as never);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('no collection selected: the pressed chip is the request scope', () => {
  it('All → no label, request (all, all)', async () => {
    renderWorkspace();
    expect(pressedChips()).toEqual(['All']);
    expect(scopeLabel()).toBeNull();
    expect(await ask()).toEqual({ collectionId: 'all', platform: 'all' });
  });

  it('click Douyin → chip Douyin, no label, request (all, douyin)', async () => {
    renderWorkspace();
    await clickChip('Douyin');
    expect(pressedChips()).toEqual(['Douyin']);
    expect(scopeLabel()).toBeNull();
    expect(await ask()).toEqual({ collectionId: 'all', platform: 'douyin' });
  });

  it('click Bilibili → chip Bilibili, no label, request (all, bilibili)', async () => {
    renderWorkspace();
    await clickChip('Bilibili');
    expect(pressedChips()).toEqual(['Bilibili']);
    expect(scopeLabel()).toBeNull();
    expect(await ask()).toEqual({ collectionId: 'all', platform: 'bilibili' });
  });
});

describe('a Douyin collection is selected: the label says what is searched, whatever the chips say', () => {
  const selectDouyinCollection = (filter: 'all' | 'douyin' = 'all') => useWorkspaceStore.setState({
    selectedCollectionId: 'c1', selectedCollectionPlatform: 'douyin', selectedPlatform: filter,
  });

  it('browse filter All → chip All, label "… · Douyin", request (c1, douyin)', async () => {
    selectDouyinCollection('all');
    renderWorkspace();
    expect(pressedChips()).toEqual(['All']);
    expect(scopeLabel()).toBe(labelText('Douyin'));
    expect(await ask()).toEqual({ collectionId: 'c1', platform: 'douyin' });
  });

  it('browse filter Douyin → chip Douyin, label "… · Douyin", request (c1, douyin)', async () => {
    selectDouyinCollection('douyin');
    renderWorkspace();
    expect(pressedChips()).toEqual(['Douyin']);
    expect(scopeLabel()).toBe(labelText('Douyin'));
    expect(await ask()).toEqual({ collectionId: 'c1', platform: 'douyin' });
  });

  it('clicking All while the filter is Douyin is browse-only: the chip moves, the label and the request do not', async () => {
    selectDouyinCollection('douyin');
    renderWorkspace();
    const before = scopeLabel();
    expect(before).toBe(labelText('Douyin'));

    await clickChip('All');

    expect(pressedChips()).toEqual(['All']);
    expect(scopeLabel()).toBe(before); // still says Douyin: the collection is still the scope
    expect(await ask()).toEqual({ collectionId: 'c1', platform: 'douyin' });
  });

  it('clicking Douyin when the filter is All: the chip moves, the label and the request do not', async () => {
    selectDouyinCollection('all');
    renderWorkspace();
    await clickChip('Douyin');
    expect(pressedChips()).toEqual(['Douyin']);
    expect(scopeLabel()).toBe(labelText('Douyin'));
    expect(await ask()).toEqual({ collectionId: 'c1', platform: 'douyin' });
  });

  it('clicking Bilibili drops the Douyin collection: chip Bilibili, no label, request (all, bilibili)', async () => {
    selectDouyinCollection('all');
    renderWorkspace();
    await clickChip('Bilibili');
    expect(pressedChips()).toEqual(['Bilibili']);
    expect(scopeLabel()).toBeNull();
    expect(await ask()).toEqual({ collectionId: 'all', platform: 'bilibili' });
  });

  it('the label\'s ✕ clears the collection: no label, request follows the chip again', async () => {
    selectDouyinCollection('all');
    renderWorkspace();
    await act(async () => { fireEvent.click(screen.getByTitle(en.scopeAll)); });
    expect(scopeLabel()).toBeNull();
    expect(pressedChips()).toEqual(['All']);
    expect(await ask()).toEqual({ collectionId: 'all', platform: 'all' });
  });
});

describe('a collection whose platform was never recorded', () => {
  it('is labelled with the platform the request really carries: the browse filter (here "All")', async () => {
    useWorkspaceStore.setState({ selectedCollectionId: 'col-1', selectedCollectionPlatform: null, selectedPlatform: 'all' });
    renderWorkspace();
    expect(scopeLabel()).toBe(labelText(en.scopeAll));
    expect(await ask()).toEqual({ collectionId: 'col-1', platform: 'all' });
  });
});

describe('the same remote id on both platforms is never sent without its platform', () => {
  it.each([
    ['douyin', 'Douyin'],
    ['bilibili', 'Bilibili'],
  ])('collection "same" of %s is labelled "… · %s" and requested with its own platform', async (owner, shown) => {
    useWorkspaceStore.setState({ selectedCollectionId: 'same', selectedCollectionPlatform: owner, selectedPlatform: 'all' });
    renderWorkspace();
    expect(scopeLabel()).toBe(labelText(shown));
    expect(await ask()).toEqual({ collectionId: 'same', platform: owner });
  });
});
