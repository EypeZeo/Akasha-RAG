import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import DeveloperPanel from './DeveloperPanel';
import { StrictMode } from 'react';

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('DeveloperPanel request boundaries', () => {
  it('does not report StrictMode effect cancellation as a backend failure', async () => {
    vi.stubGlobal('fetch', vi.fn().mockImplementation(async () => new Response('{"enabled":false}')));
    render(<StrictMode><DeveloperPanel /></StrictMode>);
    await screen.findByRole('button', { name: '启用开发者模式' });
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('rejects an HTTP failure without treating its JSON as metrics', async () => {
    const readErrorJson = vi.fn().mockResolvedValue({ detail: 'Not Found' });
    vi.stubGlobal('fetch', vi.fn()
      .mockResolvedValueOnce(new Response('{"enabled":true}'))
      .mockResolvedValue({ ok: false, status: 404, json: readErrorJson }));
    render(<DeveloperPanel />);
    expect(await screen.findByText('获取监控数据失败')).toBeTruthy();
    expect(readErrorJson).not.toHaveBeenCalled();
    expect(document.body.textContent).not.toContain('NaN');
  });

  it('does not overlap slow metric refreshes and aborts all requests on unmount', async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn().mockResolvedValueOnce(new Response('{"enabled":true}'))
      .mockImplementation(() => new Promise(() => {}));
    vi.stubGlobal('fetch', fetchMock);
    const { unmount } = render(<DeveloperPanel />);
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(fetchMock).toHaveBeenCalledTimes(5);
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(fetchMock).toHaveBeenCalledTimes(5);
    const signals = fetchMock.mock.calls.slice(1).map(call => call[1].signal as AbortSignal);
    unmount();
    expect(signals.every(signal => signal.aborted)).toBe(true);
  });

  it('shows a settings failure and permits only one concurrent mode toggle', async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce({ ok: false, status: 503 })
      .mockImplementation(() => new Promise(() => {}));
    vi.stubGlobal('fetch', fetchMock);
    const { unmount } = render(<DeveloperPanel />);
    expect(await screen.findByRole('alert')).toHaveTextContent('无法连接到后端服务');
    const enable = screen.getByRole('button', { name: '启用开发者模式' });
    fireEvent.click(enable);
    fireEvent.click(enable);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    const signal = fetchMock.mock.calls[1][1].signal as AbortSignal;
    unmount();
    expect(signal.aborted).toBe(true);
  });
});
