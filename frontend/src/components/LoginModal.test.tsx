import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, cleanup, act, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach } from 'vitest';
import LoginModal from './LoginModal';
import { I18nProvider, TRANSLATIONS } from '../i18n';
import * as api from '../api';

vi.mock('../api');

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

const platformStatus = (loggedIn: api.PlatformKind[] = []): api.PlatformInfo[] => [
  { platform: 'douyin', name: 'Douyin', is_logged_in: loggedIn.includes('douyin'), status: 'idle' },
  { platform: 'bilibili', name: 'Bilibili', is_logged_in: loggedIn.includes('bilibili'), status: 'idle' },
  { platform: 'zhihu', name: 'Zhihu', is_logged_in: loggedIn.includes('zhihu'), status: 'idle' },
];

function setup(initialPlatform: api.PlatformKind = 'douyin', onClose = vi.fn(), onSuccess = vi.fn()) {
  render(
    <I18nProvider>
      <LoginModal onClose={onClose} onSuccess={onSuccess} initialPlatform={initialPlatform} />
    </I18nProvider>,
  );
  return { onClose, onSuccess };
}

describe('LoginModal', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.listPlatforms).mockResolvedValue({ success: true, platforms: platformStatus() });
    vi.mocked(api.douyinGenerateQr).mockResolvedValue({
      success: true,
      data: { qrcode_image_base64: 'ZmFrZS1xcg==', status: 'pending', expires_in: 120 },
    });
    vi.mocked(api.bilibiliGenerateQr).mockResolvedValue({
      success: true,
      data: { qrcode_key: 'key', qrcode_url: 'https://example.test/qr', qrcode_image_base64: 'ZmFrZS1xcg==', expires_in: 120 },
    });
    vi.mocked(api.zhihuLoginStart).mockResolvedValue({ success: true, status: 'pending', message: 'waiting' });
    vi.mocked(api.loginCancel).mockResolvedValue({ success: true, message: '', status: 'idle' });
    vi.mocked(api.zhihuLoginCancel).mockResolvedValue({ success: true, message: '', status: 'idle' });
  });

  it('waits for platform status before starting the default QR flow', async () => {
    setup();
    expect(screen.getByRole('dialog')).toBeTruthy();
    await waitFor(() => expect(api.douyinGenerateQr).toHaveBeenCalledTimes(1));
    expect(screen.getByText(TRANSLATIONS.en.loginDouyin)).toBeTruthy();
  });

  it('shows a retryable error when Douyin QR generation returns no QR image', async () => {
    vi.mocked(api.douyinGenerateQr).mockResolvedValue({
      success: false,
      data: { qrcode_image_base64: '', status: 'failed', expires_in: 0 },
    });
    setup();

    await waitFor(() => expect(screen.getAllByText(TRANSLATIONS.en.networkError).length).toBeGreaterThan(0));
    expect(screen.getByText(TRANSLATIONS.en.retry)).toBeTruthy();
  });

  it('disables logged-in tabs and selects the first available platform', async () => {
    vi.mocked(api.listPlatforms).mockResolvedValue({ success: true, platforms: platformStatus(['douyin']) });
    setup();

    await waitFor(() => expect(api.bilibiliGenerateQr).toHaveBeenCalledTimes(1));
    const douyin = screen.getByRole('button', { name: TRANSLATIONS.en.platformDouyin });
    expect(douyin).toHaveProperty('disabled', true);
    expect(api.douyinGenerateQr).not.toHaveBeenCalled();
  });

  it('does not generate a QR code when every platform is already logged in', async () => {
    vi.mocked(api.listPlatforms).mockResolvedValue({ success: true, platforms: platformStatus(['douyin', 'bilibili', 'zhihu']) });
    setup();

    await waitFor(() => expect(screen.getByText(TRANSLATIONS.en.allPlatformsLoggedIn)).toBeTruthy());
    expect(api.douyinGenerateQr).not.toHaveBeenCalled();
    expect(api.bilibiliGenerateQr).not.toHaveBeenCalled();
    expect(api.zhihuLoginStart).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: TRANSLATIONS.en.platformDouyin })).toHaveProperty('disabled', true);
    expect(screen.getByRole('button', { name: TRANSLATIONS.en.platformBilibili })).toHaveProperty('disabled', true);
    expect(screen.getByRole('button', { name: TRANSLATIONS.en.platformZhihu })).toHaveProperty('disabled', true);
  });

  it('ignores a late Zhihu QR response after switching platforms', async () => {
    let resolveZhihu!: (value: { success: boolean; status: string; message: string; qrcode_image_base64: string }) => void;
    vi.mocked(api.zhihuLoginStart).mockReturnValue(new Promise((resolve) => { resolveZhihu = resolve; }));
    setup('zhihu');

    await waitFor(() => expect(api.zhihuLoginStart).toHaveBeenCalledTimes(1));
    await userEvent.click(screen.getByRole('button', { name: TRANSLATIONS.en.platformBilibili }));
    await waitFor(() => expect(api.bilibiliGenerateQr).toHaveBeenCalledTimes(1));
    resolveZhihu({ success: true, status: 'pending', message: 'late', qrcode_image_base64: 'emhpaHUtcXI=' });

    await waitFor(() => expect(screen.queryByAltText('Zhihu QR Code')).toBeNull());
    expect(screen.getByAltText('Bilibili QR Code')).toBeTruthy();
  });

  it('tells the user that Zhihu QR retrieval is still in progress until an image arrives', async () => {
    vi.mocked(api.zhihuLoginStart).mockResolvedValue({ success: true, status: 'pending', message: 'please scan' });
    setup('zhihu');

    await waitFor(() => expect(api.zhihuLoginStart).toHaveBeenCalledTimes(1));
    expect(screen.getAllByText(TRANSLATIONS.en.zhihuLoginOpening).length).toBeGreaterThan(0);
    expect(screen.queryByText(TRANSLATIONS.en.zhihuLoginWaiting)).toBeNull();
  });

  it('removes the Douyin QR and scan steps while syncing and keeps a single status message', async () => {
    vi.mocked(api.loginStatus)
      .mockResolvedValueOnce({ status: 'syncing', message: 'syncing' })
      .mockResolvedValue({ status: 'logged_in', message: 'done' });
    vi.useFakeTimers();
    const { onSuccess } = setup();
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(screen.getByAltText('Douyin QR Code')).toBeTruthy();

    await act(async () => { await vi.advanceTimersByTimeAsync(1500); });

    expect(screen.queryByAltText('Douyin QR Code')).toBeNull();
    expect(screen.queryByText(TRANSLATIONS.en.douyinStep2)).toBeNull();
    expect(screen.getAllByText(TRANSLATIONS.en.loginSyncing)).toHaveLength(1);
    expect(onSuccess).not.toHaveBeenCalled();

    await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
    expect(screen.getAllByText(TRANSLATIONS.en.loginSuccessDone)).toHaveLength(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(900); });
    expect(onSuccess).toHaveBeenCalledTimes(1);
  });

  it.each(['douyin', 'bilibili', 'zhihu'] as const)(
    'removes the %s QR and scan steps on confirmation without changing delayed completion',
    async (platform) => {
      vi.mocked(api.loginStatus).mockResolvedValue({ status: 'logged_in', message: 'done' });
      vi.mocked(api.bilibiliPollQr).mockResolvedValue({ success: true, status: 'confirmed', message: 'done' });
      vi.mocked(api.zhihuLoginStart).mockResolvedValue({ success: true, status: 'pending', message: 'scan', qrcode_image_base64: 'ZmFrZS1xcg==' });
      vi.mocked(api.zhihuLoginStatus).mockResolvedValue({ status: 'logged_in', message: 'done' });
      vi.useFakeTimers();
      const { onSuccess } = setup(platform);
      const qrAlt = platform === 'douyin' ? 'Douyin QR Code' : platform === 'bilibili' ? 'Bilibili QR Code' : 'Zhihu QR Code';
      const successText = platform === 'douyin' ? TRANSLATIONS.en.loginSuccessDone : platform === 'bilibili' ? TRANSLATIONS.en.bilibiliLoginSuccess : TRANSLATIONS.en.zhihuLoginSuccess;
      const scanStep = platform === 'douyin' ? TRANSLATIONS.en.douyinStep2 : platform === 'bilibili' ? TRANSLATIONS.en.biliStep2 : TRANSLATIONS.en.zhihuStep1;
      await act(async () => { await vi.advanceTimersByTimeAsync(0); });
      expect(screen.getByAltText(qrAlt)).toBeTruthy();

      await act(async () => { await vi.advanceTimersByTimeAsync(1500); });

      expect(screen.queryByAltText(qrAlt)).toBeNull();
      expect(screen.queryByText(scanStep)).toBeNull();
      expect(screen.getAllByText(successText)).toHaveLength(1);
      expect(screen.getByRole('button', { name: TRANSLATIONS.en.close })).toBeTruthy();
      expect(onSuccess).not.toHaveBeenCalled();
      await act(async () => { await vi.advanceTimersByTimeAsync(900); });
      expect(onSuccess).toHaveBeenCalledTimes(1);
    },
  );

  it('does not show a Zhihu QR placeholder when the start response is already logged in', async () => {
    vi.mocked(api.zhihuLoginStart).mockResolvedValue({ success: true, status: 'logged_in', message: 'done' });
    setup('zhihu');

    await screen.findByRole('heading', { name: TRANSLATIONS.en.zhihuLoginSuccess });

    expect(screen.queryByAltText('Zhihu QR Code')).toBeNull();
    expect(screen.queryByText(TRANSLATIONS.en.zhihuStep1)).toBeNull();
    expect(screen.getAllByText(TRANSLATIONS.en.zhihuLoginSuccess)).toHaveLength(1);
  });

  it('clicking the close/cancel button calls onClose', async () => {
    const { onClose } = setup();
    await waitFor(() => expect(screen.getByText(TRANSLATIONS.en.cancelLogin)).toBeTruthy());
    await userEvent.click(screen.getByText(TRANSLATIONS.en.cancelLogin));
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
  });

  it.each(['douyin', 'bilibili', 'zhihu'] as const)(
    'automatically renews expired %s QR codes only three times and allows manual renewal afterwards',
    async (platform) => {
      vi.mocked(api.loginStatus).mockResolvedValue({ status: 'expired', message: 'expired' });
      vi.mocked(api.bilibiliPollQr).mockResolvedValue({ success: true, status: 'expired', message: 'expired' });
      vi.mocked(api.zhihuLoginStatus).mockResolvedValue({ status: 'expired', message: 'expired' });
      vi.mocked(api.zhihuLoginStart).mockResolvedValue({ success: true, status: 'pending', message: 'scan', qrcode_image_base64: 'b2xk' });
      vi.useFakeTimers();
      setup(platform);
      await act(async () => { await vi.advanceTimersByTimeAsync(0); });
      const generate = platform === 'douyin' ? api.douyinGenerateQr : platform === 'bilibili' ? api.bilibiliGenerateQr : api.zhihuLoginStart;
      for (let cycle = 0; cycle < 4; cycle++) {
        await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
      }
      expect(generate).toHaveBeenCalledTimes(4);
      await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
      expect(generate).toHaveBeenCalledTimes(4);
      expect(screen.queryByAltText(platform === 'douyin' ? 'Douyin QR Code' : platform === 'bilibili' ? 'Bilibili QR Code' : 'Zhihu QR Code')).toBeNull();
      const retry = platform === 'douyin' ? TRANSLATIONS.en.qrExpiredClickRefresh : TRANSLATIONS.en.retry;
      await act(async () => { fireEvent.click(screen.getByRole('button', { name: retry })); });
      expect(generate).toHaveBeenCalledTimes(5);
    },
  );

  it.each(['douyin', 'bilibili', 'zhihu'] as const)(
    'clears the expired %s QR while its replacement is still being generated',
    async (platform) => {
      vi.mocked(api.loginStatus).mockResolvedValue({ status: 'expired', message: 'expired' });
      vi.mocked(api.bilibiliPollQr).mockResolvedValue({ success: true, status: 'expired', message: 'expired' });
      vi.mocked(api.zhihuLoginStatus).mockResolvedValue({ status: 'expired', message: 'expired' });
      vi.mocked(api.zhihuLoginStart).mockResolvedValue({ success: true, status: 'pending', message: 'scan', qrcode_image_base64: 'b2xk' });
      vi.useFakeTimers();
      setup(platform);
      await act(async () => { await vi.advanceTimersByTimeAsync(0); });
      const qrAlt = platform === 'douyin' ? 'Douyin QR Code' : platform === 'bilibili' ? 'Bilibili QR Code' : 'Zhihu QR Code';
      expect(screen.getByAltText(qrAlt)).toBeTruthy();
      if (platform === 'douyin') vi.mocked(api.douyinGenerateQr).mockImplementationOnce(() => new Promise(() => {}));
      else if (platform === 'bilibili') vi.mocked(api.bilibiliGenerateQr).mockImplementationOnce(() => new Promise(() => {}));
      else vi.mocked(api.zhihuLoginStart).mockImplementationOnce(() => new Promise(() => {}));
      await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
      expect(screen.queryByAltText(qrAlt)).toBeNull();
      if (platform === 'zhihu') {
        expect(screen.getAllByText(TRANSLATIONS.en.zhihuLoginOpening).length).toBeGreaterThan(0);
        expect(screen.queryByText(TRANSLATIONS.en.zhihuLoginWaiting)).toBeNull();
      }
    },
  );

  it.each(['douyin', 'zhihu'] as const)('replaces the %s QR and removes it during provider-side rotation', async (platform) => {
    const poll = platform === 'douyin' ? vi.mocked(api.loginStatus) : vi.mocked(api.zhihuLoginStatus);
    poll.mockResolvedValueOnce({ status: 'pending', message: 'new', qrcode_image_base64: 'bmV3' })
      .mockResolvedValueOnce({ status: 'pending', message: 'loading' });
    vi.mocked(api.zhihuLoginStart).mockResolvedValue({ success: true, status: 'pending', message: 'scan', qrcode_image_base64: 'b2xk' });
    vi.useFakeTimers();
    setup(platform);
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    const qrAlt = platform === 'douyin' ? 'Douyin QR Code' : 'Zhihu QR Code';
    await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
    expect(screen.getByAltText(qrAlt)).toHaveAttribute('src', 'data:image/png;base64,bmV3');
    await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
    expect(screen.queryByAltText(qrAlt)).toBeNull();
  });

  it.each(['douyin', 'bilibili', 'zhihu'] as const)('does not automatically retry a failed %s session', async (platform) => {
    vi.mocked(api.loginStatus).mockResolvedValue({ status: 'failed', message: 'failed' });
    vi.mocked(api.bilibiliPollQr).mockResolvedValue({ success: false, status: 'failed', message: 'failed' });
    vi.mocked(api.zhihuLoginStatus).mockResolvedValue({ status: 'failed', message: 'failed' });
    vi.mocked(api.zhihuLoginStart).mockResolvedValue({ success: true, status: 'pending', message: 'scan', qrcode_image_base64: 'b2xk' });
    vi.useFakeTimers();
    setup(platform);
    await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
    await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
    const generate = platform === 'douyin' ? api.douyinGenerateQr : platform === 'bilibili' ? api.bilibiliGenerateQr : api.zhihuLoginStart;
    expect(generate).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('button', { name: TRANSLATIONS.en.retry })).toBeTruthy();
    expect(screen.queryByAltText(platform === 'douyin' ? 'Douyin QR Code' : platform === 'bilibili' ? 'Bilibili QR Code' : 'Zhihu QR Code')).toBeNull();
  });

  it('does not restart a failed Zhihu start response or poll it as a pending session', async () => {
    vi.mocked(api.zhihuLoginStart).mockResolvedValue({ success: false, status: 'failed', message: 'provider unavailable' });
    vi.useFakeTimers();
    setup('zhihu');
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(api.zhihuLoginStart).toHaveBeenCalledTimes(1);
    expect(api.zhihuLoginStatus).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: TRANSLATIONS.en.retry })).toBeTruthy();
    expect(screen.getAllByText('provider unavailable').length).toBeGreaterThan(0);
  });

  it('ignores an old Bilibili poll and uses the replacement QR key after switching away and back', async () => {
    let finishOldPoll!: (value: Awaited<ReturnType<typeof api.bilibiliPollQr>>) => void;
    vi.mocked(api.bilibiliPollQr).mockReturnValueOnce(new Promise((resolve) => { finishOldPoll = resolve; }))
      .mockResolvedValue({ success: true, status: 'pending', message: 'scan' });
    vi.mocked(api.bilibiliGenerateQr).mockResolvedValueOnce({
      success: true, data: { qrcode_key: 'old-key', qrcode_url: '', qrcode_image_base64: 'b2xk', expires_in: 120 },
    }).mockResolvedValue({
      success: true, data: { qrcode_key: 'new-key', qrcode_url: '', qrcode_image_base64: 'bmV3', expires_in: 120 },
    });
    vi.useFakeTimers();
    setup('bilibili');
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
    expect(api.bilibiliPollQr).toHaveBeenLastCalledWith('old-key');
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: TRANSLATIONS.en.platformZhihu })); });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: TRANSLATIONS.en.platformBilibili })); });
    await act(async () => { finishOldPoll({ success: true, status: 'expired', message: 'expired' }); });
    expect(api.bilibiliGenerateQr).toHaveBeenCalledTimes(2);
    expect(screen.getByAltText('Bilibili QR Code')).toHaveAttribute('src', 'data:image/png;base64,bmV3');
    await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
    expect(api.bilibiliPollQr).toHaveBeenLastCalledWith('new-key');
  });

  it.each(['douyin', 'bilibili', 'zhihu'] as const)('ignores a late %s success response after closing', async (platform) => {
    let finishPoll!: () => void;
    if (platform === 'douyin') vi.mocked(api.loginStatus).mockReturnValue(new Promise((resolve) => { finishPoll = () => resolve({ status: 'logged_in', message: 'done' }); }));
    else if (platform === 'bilibili') vi.mocked(api.bilibiliPollQr).mockReturnValue(new Promise((resolve) => { finishPoll = () => resolve({ success: true, status: 'confirmed', message: 'done' }); }));
    else vi.mocked(api.zhihuLoginStatus).mockReturnValue(new Promise((resolve) => { finishPoll = () => resolve({ status: 'logged_in', message: 'done' }); }));
    vi.useFakeTimers();
    const { onClose, onSuccess } = setup(platform);
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: TRANSLATIONS.en.cancelLogin })); });
    await act(async () => { finishPoll(); await vi.advanceTimersByTimeAsync(900); });
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(onSuccess).not.toHaveBeenCalled();
  });

  it.each(['douyin', 'bilibili', 'zhihu'] as const)('stops %s polling after three consecutive network failures', async (platform) => {
    vi.mocked(api.loginStatus).mockRejectedValue(new Error('offline'));
    vi.mocked(api.bilibiliPollQr).mockRejectedValue(new Error('offline'));
    vi.mocked(api.zhihuLoginStatus).mockRejectedValue(new Error('offline'));
    vi.useFakeTimers();
    setup(platform);
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    for (let attempt = 0; attempt < 3; attempt++) {
      await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
    }
    const poll = platform === 'douyin' ? api.loginStatus : platform === 'bilibili' ? api.bilibiliPollQr : api.zhihuLoginStatus;
    expect(poll).toHaveBeenCalledTimes(3);
    await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
    expect(poll).toHaveBeenCalledTimes(3);
    expect(screen.getByRole('button', { name: TRANSLATIONS.en.retry })).toBeTruthy();
    expect(screen.queryByAltText(platform === 'douyin' ? 'Douyin QR Code' : platform === 'bilibili' ? 'Bilibili QR Code' : 'Zhihu QR Code')).toBeNull();
  });

  it('does not resume an awaiting platform switch after closing the modal', async () => {
    let finishCancel!: (value: Awaited<ReturnType<typeof api.zhihuLoginCancel>>) => void;
    vi.mocked(api.zhihuLoginCancel).mockReturnValue(new Promise((resolve) => { finishCancel = resolve; }));
    const { onClose } = setup('zhihu');
    await waitFor(() => expect(api.zhihuLoginStart).toHaveBeenCalledTimes(1));
    await userEvent.click(screen.getByRole('button', { name: TRANSLATIONS.en.platformBilibili }));
    await userEvent.click(screen.getByRole('button', { name: TRANSLATIONS.en.cancelLogin }));
    await act(async () => { finishCancel({ success: true, status: 'idle', message: '' }); });
    expect(api.bilibiliGenerateQr).not.toHaveBeenCalled();
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it.each(['douyin', 'zhihu'] as const)('cancels a late %s start after closing before generation finishes', async (platform) => {
    let finishStart!: () => void;
    if (platform === 'douyin') vi.mocked(api.douyinGenerateQr).mockReturnValueOnce(new Promise((resolve) => { finishStart = () => resolve({ success: true, data: { status: 'pending', qrcode_image_base64: 'b2xk', expires_in: 120 } }); }));
    else vi.mocked(api.zhihuLoginStart).mockReturnValueOnce(new Promise((resolve) => { finishStart = () => resolve({ success: true, status: 'pending', message: 'scan', qrcode_image_base64: 'b2xk' }); }));
    const { onClose } = setup(platform);
    const generate = platform === 'douyin' ? api.douyinGenerateQr : api.zhihuLoginStart;
    const cancel = platform === 'douyin' ? api.loginCancel : api.zhihuLoginCancel;
    await waitFor(() => expect(generate).toHaveBeenCalledTimes(1));
    await userEvent.click(screen.getByRole('button', { name: TRANSLATIONS.en.cancelLogin }));
    expect(onClose).toHaveBeenCalledTimes(1);
    await act(async () => { finishStart(); });
    expect(cancel).toHaveBeenCalledTimes(2);
    expect(screen.queryByAltText(platform === 'douyin' ? 'Douyin QR Code' : 'Zhihu QR Code')).toBeNull();
  });

  it.each(['douyin', 'zhihu'] as const)('does not cancel the new %s worker when an old generation finishes on the same platform', async (platform) => {
    let finishStart!: () => void;
    if (platform === 'douyin') vi.mocked(api.douyinGenerateQr).mockReturnValueOnce(new Promise((resolve) => { finishStart = () => resolve({ success: true, data: { status: 'pending', qrcode_image_base64: 'b2xk', expires_in: 120 } }); }));
    else vi.mocked(api.zhihuLoginStart).mockReturnValueOnce(new Promise((resolve) => { finishStart = () => resolve({ success: true, status: 'pending', message: 'scan', qrcode_image_base64: 'b2xk' }); }));
    setup(platform);
    const generate = platform === 'douyin' ? api.douyinGenerateQr : api.zhihuLoginStart;
    const cancel = platform === 'douyin' ? api.loginCancel : api.zhihuLoginCancel;
    await waitFor(() => expect(generate).toHaveBeenCalledTimes(1));
    await userEvent.click(screen.getByRole('button', { name: TRANSLATIONS.en.platformBilibili }));
    await waitFor(() => expect(api.bilibiliGenerateQr).toHaveBeenCalledTimes(1));
    await userEvent.click(screen.getByRole('button', { name: platform === 'douyin' ? TRANSLATIONS.en.platformDouyin : TRANSLATIONS.en.platformZhihu }));
    await waitFor(() => expect(generate).toHaveBeenCalledTimes(2));
    const cancellations = vi.mocked(cancel).mock.calls.length;
    await act(async () => { finishStart(); });
    expect(cancel).toHaveBeenCalledTimes(cancellations);
    if (platform === 'douyin') expect(screen.getByAltText('Douyin QR Code')).toHaveAttribute('src', 'data:image/png;base64,ZmFrZS1xcg==');
    else expect(screen.queryByAltText('Zhihu QR Code')).toBeNull();
  });

  it.each(['syncing', 'logged_in'] as const)('recognizes authenticated Douyin %s generation replies without a QR image', async (status) => {
    vi.mocked(api.douyinGenerateQr).mockResolvedValue({ success: false, data: { status, expires_in: 0 } });
    vi.mocked(api.loginStatus).mockResolvedValue({ status: 'logged_in', message: 'done' });
    vi.useFakeTimers();
    const { onSuccess } = setup();
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(screen.queryByAltText('Douyin QR Code')).toBeNull();
    expect(screen.queryByRole('button', { name: TRANSLATIONS.en.retry })).toBeNull();
    expect(screen.queryByText(TRANSLATIONS.en.douyinStep2)).toBeNull();
    expect(screen.getAllByText(status === 'syncing' ? TRANSLATIONS.en.loginSyncing : TRANSLATIONS.en.loginSuccessDone)).toHaveLength(1);
    if (status === 'syncing') await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
    await act(async () => { await vi.advanceTimersByTimeAsync(900); });
    expect(onSuccess).toHaveBeenCalledTimes(1);
  });

  it('keeps an authenticated Douyin session completed when syncing status polls lose network connectivity', async () => {
    vi.mocked(api.loginStatus).mockResolvedValueOnce({ status: 'syncing', message: 'syncing' }).mockRejectedValue(new Error('offline'));
    vi.useFakeTimers();
    const { onSuccess } = setup();
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    for (let attempt = 0; attempt < 4; attempt++) {
      await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
    }
    expect(screen.queryByAltText('Douyin QR Code')).toBeNull();
    expect(screen.queryByRole('button', { name: TRANSLATIONS.en.retry })).toBeNull();
    expect(api.douyinGenerateQr).toHaveBeenCalledTimes(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(900); });
    expect(onSuccess).toHaveBeenCalledTimes(1);
  });

  it('stops Bilibili polling on the API error status and exposes manual retry', async () => {
    vi.mocked(api.bilibiliPollQr).mockResolvedValue({ success: false, status: 'error', message: 'upstream unavailable' });
    vi.useFakeTimers();
    setup('bilibili');
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
    expect(screen.getByRole('button', { name: TRANSLATIONS.en.retry })).toBeTruthy();
    expect(screen.queryByAltText('Bilibili QR Code')).toBeNull();
    await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
    expect(api.bilibiliPollQr).toHaveBeenCalledTimes(1);
  });
});
