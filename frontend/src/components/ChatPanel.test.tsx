import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, act, fireEvent } from '@testing-library/react';
import ChatPanel from './ChatPanel';
import { I18nProvider, TRANSLATIONS } from '../i18n';
import * as api from '../api';
import * as chatExport from '../utils/chatExport';
import { useWorkspaceStore } from '../store/workspace';

vi.mock('../api');
vi.mock('../utils/chatExport');

vi.mock('@tanstack/react-virtual', async () => {
  const { createVirtualizerModuleMock } = await import('../test/virtualizerFake');
  return createVirtualizerModuleMock();
});

// ChatMessageRow's citation/markdown/code-block rendering is already covered
// by ChatMessageRow.test.tsx — treat it as a black box here so these tests
// stay focused on ChatPanel's own state machine (generations, RAF batching,
// abort, preflight gate), not on re-deriving citation-matching behavior.
vi.mock('./ChatMessageRow', () => ({
  default: ({ msg }: { msg: { role: string; content: string; isStreaming?: boolean; interrupted?: boolean; sources?: unknown[] } }) => (
    <div
      data-testid="msg-row"
      data-role={msg.role}
      data-streaming={String(!!msg.isStreaming)}
      data-interrupted={String(!!msg.interrupted)}
      data-sources={msg.sources ? String(msg.sources.length) : ''}
    >
      {msg.content}
    </div>
  ),
}));

interface StreamEvent {
  _event: string;
  [key: string]: unknown;
}

/** A hand-controllable async generator standing in for api.chatAskStream's
 *  return value, so a test can drive the exact sequence/timing of SSE events
 *  a component sees without touching real SSE parsing (covered separately in
 *  api.chatAskStream.test.ts). */
function makeControllableStream() {
  const queue: StreamEvent[] = [];
  let notify: (() => void) | null = null;
  let endedWith: { error: unknown } | 'end' | null = null;
  async function* gen(): AsyncGenerator<StreamEvent> {
    while (true) {
      if (queue.length) {
        yield queue.shift()!;
        continue;
      }
      if (endedWith === 'end') return;
      if (endedWith && 'error' in endedWith) throw endedWith.error;
      await new Promise<void>(res => { notify = res; });
    }
  }
  return {
    stream: gen(),
    push: (event: StreamEvent) => { queue.push(event); notify?.(); notify = null; },
    end: () => { endedWith = 'end'; notify?.(); notify = null; },
    fail: (error: unknown) => { endedWith = { error }; notify?.(); notify = null; },
  };
}

interface SetupProps {
  collectionId: string;
  platform?: string;
  statsRefreshKey: number;
  activeSessionId?: number | null;
  onSelectSession?: (id: number | null) => void;
  active?: boolean;
  onOpenSettings: () => void;
}

function buildElement(propsOverride: Partial<SetupProps>) {
  return (
    <I18nProvider>
      <ChatPanel
        collectionId={propsOverride.collectionId ?? 'all'}
        platform={propsOverride.platform}
        statsRefreshKey={propsOverride.statsRefreshKey ?? 0}
        activeSessionId={propsOverride.activeSessionId}
        onSelectSession={propsOverride.onSelectSession ?? vi.fn()}
        active={propsOverride.active ?? true}
        onOpenSettings={propsOverride.onOpenSettings ?? vi.fn()}
      />
    </I18nProvider>
  );
}

/** Mount-time effects (getKnowledgeStats, ChatSessionDrawer's listSessions)
 *  resolve asynchronously right after the synchronous render() call returns
 *  — awaiting an async act() here flushes those before the test proceeds,
 *  instead of leaving them to land un-wrapped during a later assertion. */
async function setup(propsOverride: Partial<SetupProps> = {}) {
  const onSelectSession = propsOverride.onSelectSession ?? vi.fn();
  const onOpenSettings = propsOverride.onOpenSettings ?? vi.fn();
  const resolvedProps = { ...propsOverride, onSelectSession, onOpenSettings };
  let renderResult!: ReturnType<typeof render>;
  await act(async () => {
    renderResult = render(buildElement(resolvedProps));
  });
  const { rerender, unmount } = renderResult;
  return {
    onSelectSession,
    onOpenSettings,
    unmount,
    rerender: (nextOverride: Partial<SetupProps> = {}) =>
      rerender(buildElement({ ...resolvedProps, ...nextOverride })),
  };
}

async function sendViaEnter(text: string) {
  const textarea = await screen.findByPlaceholderText(TRANSLATIONS.en.inputPlaceholder);
  fireEvent.change(textarea, { target: { value: text } });
  // handleSendMessage's first await (getSettingsStatus) means control
  // returns to the caller before the rest of the handler has run — flush it
  // here rather than leaving it to land un-wrapped during a later assertion.
  await act(async () => {
    fireEvent.keyDown(textarea, { key: 'Enter' });
  });
  return textarea;
}

beforeEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
  useWorkspaceStore.setState({
    activeTab: 'chat',
    selectedCollectionId: 'all',
    selectedPlatform: 'all',
    activeSessionId: null,
  });
  vi.mocked(api.getKnowledgeStats).mockResolvedValue({ success: true, video_cache: { done: 0 }, detail: { note: { done: 0 } } });
  vi.mocked(api.listSessions).mockResolvedValue({ success: true, items: [] });
  vi.mocked(api.getSettingsStatus).mockResolvedValue({ success: true, chat_ready: true, ingest_ready: true });
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

// Fake-timer convention for this file: if a test needs to reconfigure fake
// timers partway through (e.g. switching `shouldAdvanceTime` on/off), call
// `vi.useRealTimers()` before the next `vi.useFakeTimers(...)` call, not just
// `vi.useFakeTimers(...)` again on top of an already-installed instance.
// Calling it again without first uninstalling does not reliably discard the
// previous config (confirmed empirically: a `shouldAdvanceTime: true`
// installation stayed in effect even after a bare `vi.useFakeTimers()` call
// with no options later in the same test) — this was the actual root cause
// of an intermittent, hard-to-reproduce failure while developing test 2
// below, before it was rewritten to avoid needing a reconfigure at all. No
// test currently in this file reconfigures fake timers mid-test, but this is
// the rule to follow if a future one needs to.

describe('ChatPanel send/stream state machine', () => {
  it('1. initial send: user message renders, sources/meta land, stream completes', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { stream, push, end } = makeControllableStream();
    const onSelectSession = vi.fn();
    vi.mocked(api.chatAskStream).mockReturnValue(stream);

    await setup({ onSelectSession });
    await sendViaEnter('hello there');

    expect(await screen.findByText('hello there')).toBeTruthy();

    await act(async () => {
      push({ _event: 'delta', text: 'Hello' });
      await vi.advanceTimersToNextFrame();
    });
    expect(await screen.findByText('Hello')).toBeTruthy();

    await act(async () => {
      push({ _event: 'sources', sources: [{ title: 'Doc A' }] });
      await vi.advanceTimersByTimeAsync(0);
    });
    await waitFor(() => {
      const rows = screen.getAllByTestId('msg-row');
      const assistantRow = rows.find(r => r.getAttribute('data-role') === 'assistant');
      expect(assistantRow?.getAttribute('data-sources')).toBe('1');
    });

    await act(async () => {
      push({ _event: 'meta', session_id: 7, latency_ms: 120 });
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(onSelectSession).toHaveBeenCalledWith(7);

    await act(async () => {
      end();
      await vi.advanceTimersByTimeAsync(0);
    });
    await waitFor(() => {
      const rows = screen.getAllByTestId('msg-row');
      const assistantRow = rows.find(r => r.getAttribute('data-role') === 'assistant');
      expect(assistantRow?.getAttribute('data-streaming')).toBe('false');
    });
  });

  it('2. delta content is batched per animation frame, not applied per SSE chunk', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { stream, push } = makeControllableStream();
    vi.mocked(api.chatAskStream).mockReturnValue(stream);

    await setup();
    await sendViaEnter('q');

    // Asserting "content hasn't landed yet" by racing a DOM query against a
    // fake clock proved flaky under full-suite contention — jsdom/vitest's
    // requestAnimationFrame scheduling under `shouldAdvanceTime: true` isn't
    // guaranteed to stay pinned at 0 elapsed time across an await boundary
    // when real CPU scheduling is delayed. Assert the deterministic,
    // timing-independent proof of batching instead: two delta chunks arrive
    // before any frame is asked to elapse, but only one requestAnimationFrame
    // call is made — the second chunk was batched into the pending buffer,
    // not scheduled as its own flush.
    const rafSpy = vi.spyOn(window, 'requestAnimationFrame');

    await act(async () => {
      push({ _event: 'delta', text: 'He' });
      push({ _event: 'delta', text: 'llo' });
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(rafSpy).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersToNextFrame();
    });
    expect(await screen.findByText('Hello')).toBeTruthy();
  });

  it('3. a stale generation\'s late events cannot corrupt a newer generation\'s message', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const streamA = makeControllableStream();
    const streamB = makeControllableStream();
    vi.mocked(api.chatAskStream)
      .mockReturnValueOnce(streamA.stream)
      .mockReturnValueOnce(streamB.stream);

    await setup();
    await sendViaEnter('message A');

    // Stop A (loading resets, generationRef nulls, input re-enables) so a
    // real UI action can trigger B — handleSendMessage's own `loading` guard
    // means B could never be sent while A is still "in flight" from the
    // component's perspective.
    const stopButton = await screen.findByTitle(TRANSLATIONS.en.stopTooltip);
    fireEvent.click(stopButton);

    await sendViaEnter('message B');
    await act(async () => {
      streamB.push({ _event: 'delta', text: 'B-content' });
      await vi.advanceTimersToNextFrame();
    });
    expect(await screen.findByText('B-content')).toBeTruthy();

    // A's mock stream deliberately never self-aborts on the signal — a late
    // event from it must be rejected by ChatPanel's own generationRef guard,
    // not by the mock stream stopping itself.
    await act(async () => {
      streamA.push({ _event: 'delta', text: 'stale' });
      await vi.advanceTimersToNextFrame();
    });
    expect(screen.queryByText('stale')).toBeNull();
    expect(screen.queryByText(/stale/)).toBeNull();
    expect(await screen.findByText('B-content')).toBeTruthy();
  });

  it('4. clicking Stop aborts the signal, marks the message interrupted, and blocks further updates from the aborted stream', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { stream, push } = makeControllableStream();
    vi.mocked(api.chatAskStream).mockReturnValue(stream);

    await setup();
    await sendViaEnter('q');

    const signal = vi.mocked(api.chatAskStream).mock.calls[0][4] as AbortSignal;
    expect(signal).toBeInstanceOf(AbortSignal);
    expect(signal.aborted).toBe(false);

    const stopButton = await screen.findByTitle(TRANSLATIONS.en.stopTooltip);
    fireEvent.click(stopButton);

    expect(signal.aborted).toBe(true);
    await waitFor(() => {
      const rows = screen.getAllByTestId('msg-row');
      const assistantRow = rows.find(r => r.getAttribute('data-role') === 'assistant');
      expect(assistantRow?.getAttribute('data-interrupted')).toBe('true');
      expect(assistantRow?.getAttribute('data-streaming')).toBe('false');
    });

    await act(async () => {
      push({ _event: 'delta', text: 'late' });
      await vi.advanceTimersToNextFrame();
    });
    expect(screen.queryByText('late')).toBeNull();
  });

  it('5a. an AbortError from the stream marks the message interrupted, not a generic failure', async () => {
    vi.mocked(api.chatAskStream).mockImplementation(() => {
      throw Object.assign(new Error('aborted'), { name: 'AbortError' });
    });

    await setup();
    await sendViaEnter('q');

    await waitFor(() => {
      const rows = screen.getAllByTestId('msg-row');
      const assistantRow = rows.find(r => r.getAttribute('data-role') === 'assistant');
      expect(assistantRow?.getAttribute('data-interrupted')).toBe('true');
    });
    expect(screen.queryByText(TRANSLATIONS.en.chatRequestFailed)).toBeNull();
  });

  it('5b. a generic error replaces the message with a system failure notice', async () => {
    vi.mocked(api.chatAskStream).mockImplementation(() => {
      throw new Error('network down');
    });

    await setup();
    await sendViaEnter('q');

    await waitFor(() => {
      const rows = screen.getAllByTestId('msg-row');
      const systemRow = rows.find(r => r.getAttribute('data-role') === 'system');
      expect(systemRow?.textContent).toBe(TRANSLATIONS.en.chatRequestFailed);
    });
  });

  it('6a. chat_ready:false blocks sending and shows the API-key-missing modal', async () => {
    vi.mocked(api.getSettingsStatus).mockResolvedValue({ success: true, chat_ready: false, ingest_ready: true });

    await setup();
    await sendViaEnter('q');

    expect(await screen.findByText(TRANSLATIONS.en.apiKeyMissingTitle)).toBeTruthy();
    expect(api.chatAskStream).not.toHaveBeenCalled();
  });

  it('6b. a preflight check that throws does not block sending (deliberately swallowed)', async () => {
    vi.mocked(api.getSettingsStatus).mockRejectedValue(new Error('offline'));
    const { stream } = makeControllableStream();
    vi.mocked(api.chatAskStream).mockReturnValue(stream);

    await setup();
    await sendViaEnter('q');

    await waitFor(() => {
      expect(api.chatAskStream).toHaveBeenCalled();
    });
  });
});

describe('ChatPanel KB-stats empty state', () => {
  it('7a. shows the plain welcome tip and no prompt chips when nothing is ingested yet', async () => {
    // beforeEach's baseline already resolves { video_cache: { done: 0 } }.
    await setup();

    expect(await screen.findByText(TRANSLATIONS.en.welcomeTip)).toBeTruthy();
    expect(screen.queryByText(TRANSLATIONS.en.promptSummary.replace(/^\S+\s/, ''))).toBeNull();
  });

  it('7b. shows count-based copy and prompt chips once content is ingested; clicking a chip sends its label', async () => {
    vi.mocked(api.getKnowledgeStats).mockResolvedValue({
      success: true, video_cache: { done: 5 }, detail: { note: { done: 0 } },
    });
    const { stream } = makeControllableStream();
    vi.mocked(api.chatAskStream).mockReturnValue(stream);

    await setup();

    const chipLabel = TRANSLATIONS.en.promptSummary.replace(/^\S+\s/, '');
    const chip = await screen.findByText(chipLabel);
    expect(screen.queryByText(TRANSLATIONS.en.welcomeTip)).toBeNull();

    await act(async () => {
      fireEvent.click(chip);
    });
    await waitFor(() => {
      expect(api.chatAskStream).toHaveBeenCalledWith(chipLabel, null, 'all', undefined, expect.any(AbortSignal));
    });
  });
});

describe('ChatPanel export menu', () => {
  async function setupWithCompletedMessage() {
    const { stream, push, end } = makeControllableStream();
    vi.mocked(api.chatAskStream).mockReturnValue(stream);
    await setup();
    await sendViaEnter('hello there');
    await act(async () => {
      push({ _event: 'delta', text: 'Hi' });
      // Ending the generator without an explicit 'done' event simulates the
      // stream connection just closing — handleSendMessage's `finally` block
      // calls flushDelta() directly on normal for-await exit, so the pending
      // delta lands without needing a real requestAnimationFrame to fire.
      end();
    });
    await waitFor(() => {
      expect(screen.getByText('Hi')).toBeTruthy();
    });
  }

  it('8a. Markdown/Word/Text menu items call the matching chatExport function with the transcript and a title', async () => {
    await setupWithCompletedMessage();

    const exportToggle = screen.getByTitle(TRANSLATIONS.en.exportChatTooltip);
    fireEvent.click(exportToggle);

    fireEvent.click(screen.getByText('Markdown (.md)'));
    const markdownMock = vi.mocked(chatExport.exportChatToMarkdown);
    expect(markdownMock).toHaveBeenCalledTimes(1);
    expect(markdownMock.mock.calls[0][0]).toEqual(
      expect.arrayContaining([expect.objectContaining({ role: 'user', content: 'hello there' })]),
    );
    expect(markdownMock.mock.calls[0][1]).toBe(TRANSLATIONS.en.sessionFileTitle);

    fireEvent.click(exportToggle);
    fireEvent.click(screen.getByText(TRANSLATIONS.en.exportDocTitle));
    expect(chatExport.exportChatToWord).toHaveBeenCalledTimes(1);

    fireEvent.click(exportToggle);
    fireEvent.click(screen.getByText(TRANSLATIONS.en.exportTxtTitle));
    expect(chatExport.exportChatToText).toHaveBeenCalledTimes(1);
  });

  it('8b. the PDF menu item renders every message unvirtualized and calls window.print', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const printSpy = vi.spyOn(window, 'print').mockImplementation(() => {});
    await setupWithCompletedMessage();

    const exportToggle = screen.getByTitle(TRANSLATIONS.en.exportChatTooltip);
    fireEvent.click(exportToggle);

    // handleExportPdf does flushSync(() => setPrintAll(true)) then two nested
    // requestAnimationFrame calls before window.print() — the inner rAF is
    // only scheduled once the outer one's callback runs, so two separate
    // advanceTimersToNextFrame() calls aren't guaranteed to catch a
    // just-scheduled-mid-tick inner callback. Advancing by two frames'
    // worth of fake time in one go processes anything scheduled along the
    // way, including the inner rAF.
    await act(async () => {
      fireEvent.click(screen.getByText(TRANSLATIONS.en.exportPdfTitle));
      await vi.advanceTimersByTimeAsync(32);
    });

    expect(printSpy).toHaveBeenCalledTimes(1);
  });
});

describe('ChatPanel composer', () => {
  it('9a. Enter submits and clears the input; Shift+Enter does not submit', async () => {
    const { stream } = makeControllableStream();
    vi.mocked(api.chatAskStream).mockReturnValue(stream);
    await setup();

    const textarea = await screen.findByPlaceholderText(TRANSLATIONS.en.inputPlaceholder) as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: 'draft' } });
    fireEvent.keyDown(textarea, { key: 'Enter', shiftKey: true });
    expect(api.chatAskStream).not.toHaveBeenCalled();
    expect(textarea.value).toBe('draft');

    await act(async () => {
      fireEvent.keyDown(textarea, { key: 'Enter' });
    });
    await waitFor(() => {
      expect(api.chatAskStream).toHaveBeenCalledTimes(1);
    });
    expect(textarea.value).toBe('');
  });

  it('9b. the textarea is disabled while loading, and a repeat Enter does not send again', async () => {
    const { stream } = makeControllableStream();
    vi.mocked(api.chatAskStream).mockReturnValue(stream);
    await setup();
    const textarea = await sendViaEnter('first message') as HTMLTextAreaElement;

    expect(textarea).toHaveProperty('disabled', true);
    fireEvent.change(textarea, { target: { value: 'second' } });
    fireEvent.keyDown(textarea, { key: 'Enter' });
    expect(api.chatAskStream).toHaveBeenCalledTimes(1);
  });

  it('9c. Alt+E opens the expanded editor, and Ctrl+Enter inside it submits and closes it', async () => {
    const { stream } = makeControllableStream();
    vi.mocked(api.chatAskStream).mockReturnValue(stream);
    await setup();

    await act(async () => {
      fireEvent.keyDown(window, { key: 'e', altKey: true });
    });
    const modalTextarea = await screen.findByPlaceholderText(TRANSLATIONS.en.expandedPlaceholder);
    fireEvent.change(modalTextarea, { target: { value: 'expanded draft' } });

    await act(async () => {
      fireEvent.keyDown(modalTextarea, { key: 'Enter', ctrlKey: true });
    });

    await waitFor(() => {
      expect(api.chatAskStream).toHaveBeenCalledWith('expanded draft', null, 'all', undefined, expect.any(AbortSignal));
    });
    expect(screen.queryByPlaceholderText(TRANSLATIONS.en.expandedPlaceholder)).toBeNull();
  });
});
