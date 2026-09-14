import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, cleanup } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach } from 'vitest';
import LoginModal from './LoginModal';
import { I18nProvider, TRANSLATIONS } from '../i18n';
import * as api from '../api';

vi.mock('../api');

afterEach(cleanup);

function setup(onClose = vi.fn(), onSuccess = vi.fn()) {
  render(
    <I18nProvider>
      <LoginModal onClose={onClose} onSuccess={onSuccess} initialPlatform="douyin" />
    </I18nProvider>,
  );
  return { onClose, onSuccess };
}

describe('LoginModal', () => {
  beforeEach(() => {
    vi.mocked(api.douyinGenerateQr).mockResolvedValue({
      success: true,
      data: { qrcode_image_base64: 'ZmFrZS1xcg==' },
    } as any);
  });

  it('opens with the dialog role and the Douyin QR flow content visible', async () => {
    setup();
    expect(screen.getByRole('dialog')).toBeTruthy();
    await waitFor(() => {
      expect(screen.getByText(TRANSLATIONS.en.scanWithDouyinApp)).toBeTruthy();
    });
  });

  it('clicking the close/cancel button calls onClose', async () => {
    const { onClose } = setup();
    await waitFor(() => {
      expect(screen.getByText(TRANSLATIONS.en.cancelLogin)).toBeTruthy();
    });
    await userEvent.click(screen.getByText(TRANSLATIONS.en.cancelLogin));
    await waitFor(() => {
      expect(onClose).toHaveBeenCalledTimes(1);
    });
  });
});
