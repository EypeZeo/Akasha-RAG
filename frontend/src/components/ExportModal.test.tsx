import { StrictMode } from 'react';
import { describe, it, expect, vi, beforeEach, afterEach, type MockInstance } from 'vitest';
import { render, screen, act, fireEvent } from '@testing-library/react';
import ExportModal from './ExportModal';
import { I18nProvider, TRANSLATIONS, useI18n } from '../i18n';
import { useExportFlow } from '../hooks/useExportFlow';
import * as api from '../api';

vi.mock('../api');

describe('ExportModal', () => {
  it('the icon-only close button has an accessible name', () => {
    render(
      <I18nProvider>
        <ExportModal
          onClose={vi.fn()}
          onExportStarted={vi.fn()}
          collectionId="all"
          collectionTitle="All"
          doneCount={0}
        />
      </I18nProvider>,
    );
    expect(screen.getByRole('button', { name: TRANSLATIONS.en.close })).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------------------------------------
// Lifecycle. The modal starts a server-side export task and picks directories asynchronously. After the modal
// itself is gone it must not touch state, call onClose (that could close a modal the user has opened since) or
// remember a directory the user abandoned. It must still call onExportStarted exactly once when the task was
// created: the task exists on the server, and the host either polls it (host still mounted) or stores it for
// the next mount (host unmounted too — useExportFlow's own behaviour).
//
// The host below is SourcesPanel's side of that contract: the real useExportFlow, plus counters for what the
// modal calls.
// ---------------------------------------------------------------------------------------------------------

const en = TRANSLATIONS.en;
const ACTIVE_EXPORT_KEY = 'akasha:active_export';
const EXPORT_DIR_KEY = 'akasha:exportDir';

interface Probe {
  closes: number;
  started: Array<[string, string]>;
}

function Host({ probe }: { probe: Probe }) {
  const { t } = useI18n();
  const flow = useExportFlow(t);
  return (
    <>
      <button onClick={flow.openExportModal}>open-export</button>
      <output data-testid="task">{flow.exportTask ? `${flow.exportTask.id}:${flow.exportTask.status}` : 'none'}</output>
      {flow.showExportModal && (
        <ExportModal
          collectionId="c1"
          collectionTitle="Col"
          doneCount={3}
          platform="douyin"
          onClose={() => { probe.closes += 1; flow.closeExportModal(); }}
          onExportStarted={(id, mode) => { probe.started.push([id, mode]); flow.startExportPolling(id, mode); }}
        />
      )}
    </>
  );
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

type Started = { task_id: string; mode: 'local' | 'browser' };

describe('ExportModal lifecycle', () => {
  let errorSpy: MockInstance;
  let probe: Probe;

  const renderHost = () => render(<I18nProvider><Host probe={probe} /></I18nProvider>);
  const openModal = () => fireEvent.click(screen.getByText('open-export'));
  const startButton = () => screen.getByRole('button', { name: new RegExp(en.startExportLocal) });
  const closeButton = () => screen.getByRole('button', { name: en.close });

  beforeEach(() => {
    probe = { closes: 0, started: [] };
    vi.mocked(api.exportBatchStart).mockReset();
    vi.mocked(api.pickDirectory).mockReset();
    vi.mocked(api.getExportProgress).mockReset().mockResolvedValue(
      { success: true, status: 'running', progress: 1, total: 5 } as never,
    );
    localStorage.setItem(EXPORT_DIR_KEY, 'D:\\out'); // a remembered folder, so a local export can start at once
    errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  afterEach(() => {
    errorSpy.mockRestore();
  });

  it('a start that lands while the modal is open hands the task over and closes the modal', async () => {
    const start = deferred<Started>();
    vi.mocked(api.exportBatchStart).mockReturnValue(start.promise as never);
    renderHost();
    openModal();
    fireEvent.click(startButton());
    await act(async () => { start.resolve({ task_id: 't0', mode: 'local' }); });
    expect(probe.started).toEqual([['t0', 'local']]);
    expect(probe.closes).toBe(1);
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('works under StrictMode: the mounted guard is re-armed after the simulated unmount', async () => {
    const start = deferred<Started>();
    vi.mocked(api.exportBatchStart).mockReturnValue(start.promise as never);
    render(<StrictMode><I18nProvider><Host probe={probe} /></I18nProvider></StrictMode>);
    openModal();
    fireEvent.click(startButton());
    await act(async () => { start.resolve({ task_id: 't3', mode: 'local' }); });
    expect(probe.started).toEqual([['t3', 'local']]);
    expect(probe.closes).toBe(1);
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('a failed start keeps the modal open and says so', async () => {
    const start = deferred<Started>();
    vi.mocked(api.exportBatchStart).mockReturnValue(start.promise as never);
    renderHost();
    openModal();
    fireEvent.click(startButton());
    await act(async () => { start.reject(new Error('offline')); });
    expect(screen.getByText(en.exportFailedRetry)).toBeTruthy();
    expect(screen.getByRole('dialog')).toBeTruthy();
    expect(probe.started).toEqual([]);
    expect(probe.closes).toBe(0);
  });

  it('a directory picked while the modal is open is used and remembered', async () => {
    const pick = deferred<{ success: boolean; path?: string }>();
    vi.mocked(api.pickDirectory).mockReturnValue(pick.promise as never);
    renderHost();
    openModal();
    fireEvent.click(screen.getByRole('button', { name: en.browse }));
    await act(async () => { pick.resolve({ success: true, path: 'D:\\picked' }); });
    expect((screen.getByRole('textbox') as HTMLInputElement).value).toBe('D:\\picked');
    expect(localStorage.getItem(EXPORT_DIR_KEY)).toBe('D:\\picked');
  });

  describe('(i) the modal is unmounted while its host is still mounted', () => {
    it('the title-bar close button still works while exporting; the task that lands later is tracked, and the old instance does not close a modal opened since', async () => {
      const start = deferred<Started>();
      vi.mocked(api.exportBatchStart).mockReturnValue(start.promise as never);
      renderHost();
      openModal();
      fireEvent.click(startButton()); // request in flight
      fireEvent.click(closeButton()); // Esc, backdrop and Cancel are disabled now — this button is not
      expect(probe.closes).toBe(1);
      expect(screen.queryByRole('dialog')).toBeNull();
      openModal(); // the user opens the export dialog again
      expect(screen.getByRole('dialog')).toBeTruthy();

      await act(async () => { start.resolve({ task_id: 't1', mode: 'local' }); });

      expect(probe.started).toEqual([['t1', 'local']]); // handed to the host ...
      expect(screen.getByTestId('task').textContent).toBe('t1:running'); // ... which polls it
      expect(JSON.parse(localStorage.getItem(ACTIVE_EXPORT_KEY)!)).toEqual({ id: 't1', mode: 'local' });
      expect(probe.closes).toBe(1); // the old instance did not call onClose again ...
      expect(screen.getByRole('dialog')).toBeTruthy(); // ... so the re-opened modal is still open
    });

    it('a directory chosen after the modal was cancelled is not remembered', async () => {
      localStorage.removeItem(EXPORT_DIR_KEY);
      const pick = deferred<{ success: boolean; path?: string }>();
      vi.mocked(api.pickDirectory).mockReturnValue(pick.promise as never);
      renderHost();
      openModal();
      fireEvent.click(screen.getByRole('button', { name: en.browse }));
      fireEvent.click(screen.getByRole('button', { name: en.cancel }));
      expect(screen.queryByRole('dialog')).toBeNull();
      await act(async () => { pick.resolve({ success: true, path: 'D:\\late' }); });
      expect(localStorage.getItem(EXPORT_DIR_KEY)).toBeNull();
    });
  });

  describe('(ii) the modal and its host are unmounted', () => {
    it('a start that lands later reaches the host once and is only stored for the next mount', async () => {
      const start = deferred<Started>();
      vi.mocked(api.exportBatchStart).mockReturnValue(start.promise as never);
      const { unmount } = renderHost();
      openModal();
      fireEvent.click(startButton());
      unmount();

      await act(async () => { start.resolve({ task_id: 't2', mode: 'browser' }); });

      expect(probe.started).toEqual([['t2', 'browser']]);
      expect(probe.closes).toBe(0);
      expect(JSON.parse(localStorage.getItem(ACTIVE_EXPORT_KEY)!)).toEqual({ id: 't2', mode: 'browser' });
      expect(api.getExportProgress).not.toHaveBeenCalled(); // nothing is polling on behalf of a gone host
    });

    it('a start that fails later does nothing at all', async () => {
      const start = deferred<Started>();
      vi.mocked(api.exportBatchStart).mockReturnValue(start.promise as never);
      const { unmount } = renderHost();
      openModal();
      fireEvent.click(startButton());
      unmount();
      await act(async () => { start.reject(new Error('offline')); });
      expect(probe.started).toEqual([]);
      expect(probe.closes).toBe(0);
      expect(localStorage.getItem(ACTIVE_EXPORT_KEY)).toBeNull();
    });
  });
});
