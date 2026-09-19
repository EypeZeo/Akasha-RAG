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

// ---------------------------------------------------------------------------
// Issue #21 — every async writer (preflight, stream, initial history, "load
// earlier") must be invalidated by every session transition (switch, new chat,
// clear, delete, unmount), and A's visible state must never survive into B.
// ---------------------------------------------------------------------------

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

type HistoryResult = { success: boolean; items: api.MessageItem[]; has_more?: boolean };

function historyItem(id: number, content: string, role: 'user' | 'assistant' = 'assistant'): api.MessageItem {
  return { id, session_id: 0, role, content, route_type: 'rag', created_at: '2026-01-01T00:00:00Z' };
}

/** Every getSessionMessages call stays pending until the test resolves or
 *  rejects it explicitly, so a test controls exactly when (and in which
 *  order) A's and B's history responses land. Keyed by session id plus the
 *  `before` cursor, so an initial load and a "load earlier" page are separate. */
function makeHistoryController() {
  const entries = new Map<string, ReturnType<typeof deferred<HistoryResult>>>();
  const keyOf = (id: number, before?: number) => `${id}:${before ?? 'initial'}`;
  const entryFor = (id: number, before?: number) => {
    const key = keyOf(id, before);
    let entry = entries.get(key);
    if (!entry) {
      entry = deferred<HistoryResult>();
      entries.set(key, entry);
    }
    return entry;
  };
  vi.mocked(api.getSessionMessages).mockImplementation((id, opts) => entryFor(id, opts?.before).promise);
  return {
    resolve: (id: number, result: HistoryResult, before?: number) => entryFor(id, before).resolve(result),
    reject: (id: number, reason: unknown, before?: number) => entryFor(id, before).reject(reason),
  };
}

/** Capture animation-frame callbacks instead of running them, so a test can
 *  hold "tokens are buffered but the frame has not fired yet" open. */
function captureAnimationFrames() {
  let nextId = 1;
  const callbacks = new Map<number, FrameRequestCallback>();
  vi.spyOn(window, 'requestAnimationFrame').mockImplementation(cb => {
    const id = nextId++;
    callbacks.set(id, cb);
    return id;
  });
  vi.spyOn(window, 'cancelAnimationFrame').mockImplementation(id => { callbacks.delete(id); });
  return {
    runPending: () => {
      const pending = [...callbacks.values()];
      callbacks.clear();
      pending.forEach(cb => cb(performance.now()));
    },
  };
}

/** Wires ChatPanel to the real zustand store exactly like Workspace.tsx does
 *  (activeSessionId / setActiveSessionId), so the stream's own `meta` ->
 *  onSelectSession -> store -> prop round trip goes through the real
 *  useSyncExternalStore render timing. */
function StoreConnectedPanel() {
  const activeSessionId = useWorkspaceStore(s => s.activeSessionId);
  const setActiveSessionId = useWorkspaceStore(s => s.setActiveSessionId);
  return (
    <I18nProvider>
      <ChatPanel
        collectionId="all"
        statsRefreshKey={0}
        activeSessionId={activeSessionId}
        onSelectSession={setActiveSessionId}
        active
        onOpenSettings={vi.fn()}
      />
    </I18nProvider>
  );
}

describe('ChatPanel session-scope invalidation (Issue #21)', () => {
  let history: ReturnType<typeof makeHistoryController>;
  // The composer's placeholder changes while loading, so it can't be located
  // by placeholder text; it is the only <textarea> while the expanded editor
  // modal is closed.
  const inputEl = () => document.querySelector('textarea') as HTMLTextAreaElement;
  const ok ={ success: true, chat_ready: true, ingest_ready: true };

  beforeEach(() => {
    history = makeHistoryController();
    vi.mocked(api.deleteSession).mockResolvedValue({ success: true });
    vi.spyOn(window, 'confirm').mockReturnValue(true);
  });

  it('S1. a second submit during the settings preflight does not start a second stream', async () => {
    const gate = deferred<typeof ok>();
    vi.mocked(api.getSettingsStatus).mockReturnValue(gate.promise);
    const { stream } = makeControllableStream();
    vi.mocked(api.chatAskStream).mockReturnValue(stream);
    await setup();

    const textarea = await screen.findByPlaceholderText(TRANSLATIONS.en.inputPlaceholder);
    fireEvent.change(textarea, { target: { value: 'q' } });
    await act(async () => { fireEvent.keyDown(textarea, { key: 'Enter' }); });
    await act(async () => { fireEvent.keyDown(textarea, { key: 'Enter' }); });
    await act(async () => { gate.resolve(ok); });

    await waitFor(() => expect(api.chatAskStream).toHaveBeenCalled());
    expect(api.chatAskStream).toHaveBeenCalledTimes(1);
    expect(screen.getAllByTestId('msg-row')).toHaveLength(2);
  });

  it('S2. switching sessions during the preflight cancels the pending send: nothing is sent or written into the new session', async () => {
    const gate = deferred<typeof ok>();
    vi.mocked(api.getSettingsStatus).mockReturnValue(gate.promise);
    vi.mocked(api.chatAskStream).mockReturnValue(makeControllableStream().stream);
    const h = await setup();

    const textarea = await screen.findByPlaceholderText(TRANSLATIONS.en.inputPlaceholder);
    fireEvent.change(textarea, { target: { value: 'pending question' } });
    await act(async () => { fireEvent.keyDown(textarea, { key: 'Enter' }); });

    await act(async () => { h.rerender({ activeSessionId: 5 }); });
    await act(async () => { history.resolve(5, { success: true, items: [historyItem(1, 'B history')], has_more: false }); });
    expect(await screen.findByText('B history')).toBeTruthy();

    await act(async () => { gate.resolve(ok); });

    expect(api.chatAskStream).not.toHaveBeenCalled();
    // The unsent draft legitimately stays in the composer; what must not
    // happen is a user/assistant row for it appearing in B's transcript.
    const rows = screen.getAllByTestId('msg-row');
    expect(rows).toHaveLength(1);
    expect(rows[0].textContent).toBe('B history');
  });

  it('S3. switching sessions mid-stream aborts the old stream\'s signal', async () => {
    const { stream } = makeControllableStream();
    vi.mocked(api.chatAskStream).mockReturnValue(stream);
    const h = await setup();
    await sendViaEnter('q');

    const signal = vi.mocked(api.chatAskStream).mock.calls[0][4] as AbortSignal;
    expect(signal.aborted).toBe(false);

    await act(async () => { h.rerender({ activeSessionId: 5 }); });
    expect(signal.aborted).toBe(true);
  });

  it('S4. a stale stream finishing after a switch cannot end the new session\'s loading state', async () => {
    const old = makeControllableStream();
    vi.mocked(api.chatAskStream).mockReturnValue(old.stream);
    const h = await setup();
    await sendViaEnter('q');

    await act(async () => { h.rerender({ activeSessionId: 5 }); });
    expect(inputEl().disabled).toBe(true);

    // The mock stream deliberately ignores the abort signal: this models the
    // old generation's tail landing late, after the switch.
    await act(async () => { old.end(); });
    expect(inputEl().disabled).toBe(true);

    await act(async () => { history.resolve(5, { success: true, items: [historyItem(1, 'B history')], has_more: false }); });
    expect(await screen.findByText('B history')).toBeTruthy();
    await waitFor(() => expect(inputEl().disabled).toBe(false));
  });

  it('S5. a late meta from the old stream can neither re-select the old session nor corrupt the new session id', async () => {
    const a = makeControllableStream();
    const b = makeControllableStream();
    vi.mocked(api.chatAskStream).mockReturnValueOnce(a.stream).mockReturnValueOnce(b.stream);
    const onSelectSession = vi.fn();
    const h = await setup({ onSelectSession });
    await sendViaEnter('from A');

    await act(async () => { h.rerender({ activeSessionId: 5 }); });
    await act(async () => { history.resolve(5, { success: true, items: [historyItem(1, 'B history')], has_more: false }); });
    await screen.findByText('B history');

    await act(async () => { a.push({ _event: 'meta', session_id: 99, latency_ms: 1 }); });
    expect(onSelectSession).not.toHaveBeenCalledWith(99);

    await sendViaEnter('from B');
    expect(vi.mocked(api.chatAskStream).mock.calls[1][1]).toBe(5);
  });

  it('S6a. tokens buffered but not yet flushed when Stop is clicked never leak into the next answer', async () => {
    const frames = captureAnimationFrames();
    const a = makeControllableStream();
    const b = makeControllableStream();
    vi.mocked(api.chatAskStream).mockReturnValueOnce(a.stream).mockReturnValueOnce(b.stream);
    await setup();
    await sendViaEnter('A');

    await act(async () => { a.push({ _event: 'delta', text: 'A-buffered' }); });
    fireEvent.click(await screen.findByTitle(TRANSLATIONS.en.stopTooltip));

    await sendViaEnter('B');
    await act(async () => { b.push({ _event: 'delta', text: 'B-token' }); });
    await act(async () => { frames.runPending(); });

    expect(await screen.findByText('B-token')).toBeTruthy();
    expect(screen.queryByText(/A-buffered/)).toBeNull();
  });

  it('S6b. tokens buffered when the user switches sessions never leak into the next answer', async () => {
    const frames = captureAnimationFrames();
    const a = makeControllableStream();
    const b = makeControllableStream();
    vi.mocked(api.chatAskStream).mockReturnValueOnce(a.stream).mockReturnValueOnce(b.stream);
    const h = await setup();
    await sendViaEnter('A');

    await act(async () => { a.push({ _event: 'delta', text: 'A-buffered' }); });
    await act(async () => { h.rerender({ activeSessionId: 5 }); });
    await act(async () => { history.resolve(5, { success: true, items: [historyItem(1, 'B history')], has_more: false }); });
    await screen.findByText('B history');

    await sendViaEnter('B');
    await act(async () => { b.push({ _event: 'delta', text: 'B-token' }); });
    await act(async () => { frames.runPending(); });

    expect(await screen.findByText('B-token')).toBeTruthy();
    expect(screen.queryByText(/A-buffered/)).toBeNull();
  });

  it('S7. an initial history response for A that arrives after B\'s cannot replace B, and the next send targets B', async () => {
    const h = await setup({ activeSessionId: 5 });
    await act(async () => { h.rerender({ activeSessionId: 6 }); });
    await act(async () => { history.resolve(6, { success: true, items: [historyItem(2, 'B-content')], has_more: false }); });
    expect(await screen.findByText('B-content')).toBeTruthy();

    await act(async () => { history.resolve(5, { success: true, items: [historyItem(1, 'A-content')], has_more: false }); });
    expect(screen.queryByText('A-content')).toBeNull();
    expect(screen.getByText('B-content')).toBeTruthy();

    vi.mocked(api.chatAskStream).mockReturnValue(makeControllableStream().stream);
    await sendViaEnter('next');
    expect(vi.mocked(api.chatAskStream).mock.calls[0][1]).toBe(6);
  });

  it('S8. a "load earlier" page for A that arrives after switching to B is not prepended into B', async () => {
    const h = await setup({ activeSessionId: 5 });
    await act(async () => { history.resolve(5, { success: true, items: [historyItem(10, 'A-newest')], has_more: true }); });
    const loadEarlier = await screen.findByText(TRANSLATIONS.en.loadEarlierMessages);
    await act(async () => { fireEvent.click(loadEarlier); });

    await act(async () => { h.rerender({ activeSessionId: 6 }); });
    await act(async () => { history.resolve(6, { success: true, items: [historyItem(20, 'B-content')], has_more: false }); });
    await screen.findByText('B-content');

    await act(async () => { history.resolve(5, { success: true, items: [historyItem(9, 'A-older')], has_more: false }, 10); });
    expect(screen.queryByText('A-older')).toBeNull();
    expect(screen.getAllByTestId('msg-row')).toHaveLength(1);
  });

  it('S9a. unmounting mid-stream aborts the request and later events never reach the parent', async () => {
    const s = makeControllableStream();
    vi.mocked(api.chatAskStream).mockReturnValue(s.stream);
    const onSelectSession = vi.fn();
    const h = await setup({ onSelectSession });
    await sendViaEnter('q');
    const signal = vi.mocked(api.chatAskStream).mock.calls[0][4] as AbortSignal;

    h.unmount();
    expect(signal.aborted).toBe(true);

    await act(async () => {
      s.push({ _event: 'meta', session_id: 99, latency_ms: 1 });
      s.push({ _event: 'delta', text: 'late' });
      s.end();
    });
    expect(onSelectSession).not.toHaveBeenCalled();
  });

  it('S9b. unmounting with a "load earlier" in flight does not throw or log when it resolves late', async () => {
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    const h = await setup({ activeSessionId: 5 });
    await act(async () => { history.resolve(5, { success: true, items: [historyItem(10, 'A-newest')], has_more: true }); });
    await act(async () => { fireEvent.click(await screen.findByText(TRANSLATIONS.en.loadEarlierMessages)); });

    h.unmount();
    await act(async () => { history.resolve(5, { success: true, items: [historyItem(9, 'A-older')], has_more: false }, 10); });
    expect(errorSpy).not.toHaveBeenCalled();
  });

  async function startStreamInSession5(onSelectSession: (id: number | null) => void) {
    vi.mocked(api.chatAskStream).mockReturnValue(makeControllableStream().stream);
    const h = await setup({ activeSessionId: 5, onSelectSession });
    await act(async () => { history.resolve(5, { success: true, items: [historyItem(1, 'old answer')], has_more: false }); });
    await screen.findByText('old answer');
    await sendViaEnter('q');
    return { h, signal: vi.mocked(api.chatAskStream).mock.calls[0][4] as AbortSignal };
  }

  it.each([
    ['the top "New chat" button', () => fireEvent.click(screen.getByTitle(TRANSLATIONS.en.clearChatDesc))],
    ['the bottom "Clear" button', () => fireEvent.click(screen.getByTitle(TRANSLATIONS.en.clearChat))],
  ])('S10. %s aborts the in-flight stream, resets the session, notifies the parent and clears the view', async (_name, trigger) => {
    const onSelectSession = vi.fn();
    const { signal } = await startStreamInSession5(onSelectSession);

    await act(async () => { trigger(); });

    expect(signal.aborted).toBe(true);
    expect(onSelectSession).toHaveBeenLastCalledWith(null);
    expect(screen.queryAllByTestId('msg-row')).toHaveLength(0);
    expect(inputEl().disabled).toBe(false);
  });

  it('S10c. the parent selecting "no session" (new chat from outside) aborts the in-flight stream and clears the view', async () => {
    const { h, signal } = await startStreamInSession5(vi.fn());

    await act(async () => { h.rerender({ activeSessionId: null }); });

    expect(signal.aborted).toBe(true);
    expect(screen.queryAllByTestId('msg-row')).toHaveLength(0);
    expect(inputEl().disabled).toBe(false);
  });

  it('S11. a stale stream\'s delta after a switch never shows up in the new session', async () => {
    const frames = captureAnimationFrames();
    const a = makeControllableStream();
    vi.mocked(api.chatAskStream).mockReturnValue(a.stream);
    const h = await setup();
    await sendViaEnter('q');

    await act(async () => { h.rerender({ activeSessionId: 5 }); });
    await act(async () => { history.resolve(5, { success: true, items: [historyItem(1, 'B history')], has_more: false }); });
    await screen.findByText('B history');

    await act(async () => { a.push({ _event: 'delta', text: 'stale' }); });
    await act(async () => { frames.runPending(); });
    expect(screen.queryByText(/stale/)).toBeNull();
  });

  it('S12. a brand-new chat that gets its session id from its own meta event (real store round trip) is not aborted or reloaded', async () => {
    const s = makeControllableStream();
    vi.mocked(api.chatAskStream).mockReturnValue(s.stream);
    await act(async () => { render(<StoreConnectedPanel />); });
    await sendViaEnter('first question');
    const signal = vi.mocked(api.chatAskStream).mock.calls[0][4] as AbortSignal;

    await act(async () => { s.push({ _event: 'delta', text: 'Part1' }); });
    await act(async () => { s.push({ _event: 'meta', session_id: 7, latency_ms: 5 }); });
    expect(useWorkspaceStore.getState().activeSessionId).toBe(7);

    await act(async () => { s.push({ _event: 'delta', text: '-Part2' }); s.end(); });

    expect(await screen.findByText('Part1-Part2')).toBeTruthy();
    expect(signal.aborted).toBe(false);
    expect(api.getSessionMessages).not.toHaveBeenCalled();
    await waitFor(() => {
      const assistantRow = screen.getAllByTestId('msg-row').find(r => r.getAttribute('data-role') === 'assistant');
      expect(assistantRow?.getAttribute('data-streaming')).toBe('false');
    });
  });

  it('S13. after a stream completes normally the next send in the same session still works', async () => {
    const a = makeControllableStream();
    const b = makeControllableStream();
    vi.mocked(api.chatAskStream).mockReturnValueOnce(a.stream).mockReturnValueOnce(b.stream);
    await setup();
    await sendViaEnter('first');
    await act(async () => { a.push({ _event: 'delta', text: 'one' }); a.end(); });
    await waitFor(() => expect(inputEl().disabled).toBe(false));

    await sendViaEnter('second');
    expect(api.chatAskStream).toHaveBeenCalledTimes(2);
  });

  it('S14a. switching to B clears A\'s messages and pagination immediately, before B\'s history returns', async () => {
    const h = await setup({ activeSessionId: 5 });
    await act(async () => { history.resolve(5, { success: true, items: [historyItem(10, 'A-content')], has_more: true }); });
    await screen.findByText('A-content');
    expect(screen.getByText(TRANSLATIONS.en.loadEarlierMessages)).toBeTruthy();

    await act(async () => { h.rerender({ activeSessionId: 6 }); });

    expect(screen.queryByText('A-content')).toBeNull();
    expect(screen.queryByText(TRANSLATIONS.en.loadEarlierMessages)).toBeNull();
    expect(inputEl().disabled).toBe(true);

    await act(async () => { history.resolve(6, { success: true, items: [historyItem(20, 'B-content')], has_more: false }); });
    expect(await screen.findByText('B-content')).toBeTruthy();
  });

  it('S14b. if B\'s history fails, A\'s messages and pagination are not restored', async () => {
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    const h = await setup({ activeSessionId: 5 });
    await act(async () => { history.resolve(5, { success: true, items: [historyItem(10, 'A-content')], has_more: true }); });
    await screen.findByText('A-content');

    await act(async () => { h.rerender({ activeSessionId: 6 }); });
    await act(async () => { history.reject(6, new Error('boom')); });

    await waitFor(() => expect(inputEl().disabled).toBe(false));
    expect(screen.queryByText('A-content')).toBeNull();
    expect(screen.queryByText(TRANSLATIONS.en.loadEarlierMessages)).toBeNull();
    expect(errorSpy).toHaveBeenCalled();
  });

  describe('deleting sessions from the drawer', () => {
    const session = (id: number, title: string): api.SessionItem => ({
      id, title, message_count: 2, last_message_at: new Date().toISOString(), created_at: new Date().toISOString(),
    });

    it('S15a. deleting the session whose history is still loading resets the panel and the parent selection', async () => {
      vi.mocked(api.listSessions).mockResolvedValue({ success: true, items: [session(6, 'Session B')] });
      await act(async () => { render(<StoreConnectedPanel />); });

      fireEvent.click(await screen.findByText('Session B'));
      await waitFor(() => expect(api.getSessionMessages).toHaveBeenCalledWith(6, expect.anything()));
      expect(useWorkspaceStore.getState().activeSessionId).toBe(6);

      await act(async () => { fireEvent.click(screen.getByTitle(TRANSLATIONS.en.deleteChat)); });
      await waitFor(() => expect(useWorkspaceStore.getState().activeSessionId).toBeNull());

      await act(async () => { history.resolve(6, { success: true, items: [historyItem(1, 'B-content')], has_more: false }); });
      expect(screen.queryByText('B-content')).toBeNull();
    });

    it('S15b. deleting an unrelated session leaves the current session alone', async () => {
      vi.mocked(api.listSessions).mockResolvedValue({ success: true, items: [session(6, 'Session B'), session(7, 'Session C')] });
      await act(async () => { render(<StoreConnectedPanel />); });

      fireEvent.click(await screen.findByText('Session B'));
      await act(async () => { history.resolve(6, { success: true, items: [historyItem(1, 'B-content')], has_more: false }); });
      await screen.findByText('B-content');

      const deleteButtons = screen.getAllByTitle(TRANSLATIONS.en.deleteChat);
      await act(async () => { fireEvent.click(deleteButtons[1]); });
      await waitFor(() => expect(api.deleteSession).toHaveBeenCalledWith(7));

      expect(useWorkspaceStore.getState().activeSessionId).toBe(6);
      expect(screen.getByText('B-content')).toBeTruthy();
    });
  });
});
