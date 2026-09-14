import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import ExportProgressCard from './ExportProgressCard';
import { I18nProvider, TRANSLATIONS } from '../i18n';
import type { ExportTaskState } from '../hooks/useExportFlow';

afterEach(cleanup);

function setup(exportTask: ExportTaskState | null, onDownload = vi.fn(), onDismiss = vi.fn()) {
  render(
    <I18nProvider>
      <ExportProgressCard exportTask={exportTask} onDismiss={onDismiss} onDownload={onDownload} />
    </I18nProvider>,
  );
  return { onDownload, onDismiss };
}

describe('ExportProgressCard', () => {
  it('renders nothing when there is no export task', () => {
    const { container } = render(
      <I18nProvider>
        <ExportProgressCard exportTask={null} onDismiss={vi.fn()} onDownload={vi.fn()} />
      </I18nProvider>,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it('shows a progress bar and count while running', () => {
    setup({ id: 't1', mode: 'local', status: 'running', progress: 3, total: 10, message: 'exporting...' });
    expect(screen.getByText('3 / 10')).toBeTruthy();
  });

  it('clicking download calls onDownload with the task id when done in browser mode', async () => {
    const { onDownload } = setup({ id: 't2', mode: 'browser', status: 'done', progress: 10, total: 10, message: '' });
    await userEvent.click(screen.getByText(TRANSLATIONS.en.downloadOrRedownload));
    expect(onDownload).toHaveBeenCalledWith('t2');
  });

  it('shows the stable failure copy, not a raw backend message, when failed', () => {
    setup({ id: 't3', mode: 'local', status: 'failed', progress: 0, total: 0, message: 'Traceback: raw backend exception' });
    expect(screen.getByText(TRANSLATIONS.en.exportFailedRetry)).toBeTruthy();
    expect(screen.queryByText(/Traceback/)).toBeNull();
  });

  it('clicking the close button calls onDismiss', async () => {
    const { onDismiss } = setup({ id: 't4', mode: 'local', status: 'running', progress: 1, total: 2, message: '' });
    await userEvent.click(screen.getByTitle(TRANSLATIONS.en.close));
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });
});
