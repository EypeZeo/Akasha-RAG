import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, cleanup, act } from '@testing-library/react';
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
});
