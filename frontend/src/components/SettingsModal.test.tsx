import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, cleanup, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach } from 'vitest';
import SettingsModal from './SettingsModal';
import LoginModal from './LoginModal';
import { I18nProvider, TRANSLATIONS } from '../i18n';
import * as api from '../api';

vi.mock('../api');

afterEach(cleanup);

function setup(onClose = vi.fn()) {
  render(
    <I18nProvider>
      <SettingsModal
        isOpen={true}
        onClose={onClose}
        logLevel="info"
        availableLevels={['debug', 'info', 'warning', 'error']}
        onLogLevelChange={vi.fn()}
        collectionsPerPage={20}
        videosPerPage={20}
        onCollectionsPerPageChange={vi.fn()}
        onVideosPerPageChange={vi.fn()}
        activityBarPosition="left"
        onActivityBarPositionChange={vi.fn()}
        theme="dawn"
        onThemeChange={vi.fn()}
        cacheMb={0}
        cacheCleaning={false}
        onCleanCache={vi.fn()}
        onOpenLogs={vi.fn()}
      />
    </I18nProvider>,
  );
  return { onClose };
}

describe('SettingsModal', () => {
  beforeEach(() => {
    vi.mocked(api.listPlatforms).mockResolvedValue({ success: true, platforms: [] } as any);
    vi.mocked(api.getDashscopeKey).mockResolvedValue({ success: true, configured: false, api_key_masked: '' } as any);
    vi.mocked(api.listChatProviders).mockResolvedValue({ success: true, providers: [], active_id: null } as any);
  });

  it('opens with the dialog role and the settings title visible', async () => {
    setup();
    expect(screen.getByRole('dialog')).toBeTruthy();
    await waitFor(() => {
      expect(screen.getByText(TRANSLATIONS.en.settingsTitle)).toBeTruthy();
    });
  });

  it('clicking the footer close button calls onClose', async () => {
    const { onClose } = setup();
    await waitFor(() => {
      expect(screen.getByText(TRANSLATIONS.en.settingsTitle)).toBeTruthy();
    });
    const closeButtons = screen.getAllByText(TRANSLATIONS.en.close);
    await userEvent.click(closeButtons[closeButtons.length - 1]);
    expect(onClose).toHaveBeenCalled();
  });

  it('opening LoginModal from within SettingsModal: Escape only closes the login modal, settings stays open', async () => {
    // Reproduces the exact real bug this batch fixes: Workspace.tsx's
    // onOpenLoginModal only sets showLoginModal, it never also closes
    // Settings -- so both are simultaneously mounted. Before the shared
    // Dialog stack, one Escape press would have fired both onClose
    // callbacks at once.
    vi.mocked(api.douyinGenerateQr).mockResolvedValue({
      success: true,
      data: { qrcode_image_base64: 'ZmFrZS1xcg==' },
    } as any);

    const onCloseSettings = vi.fn();
    const onCloseLogin = vi.fn();

    render(
      <I18nProvider>
        <SettingsModal
          isOpen={true}
          onClose={onCloseSettings}
          logLevel="info"
          availableLevels={['debug', 'info', 'warning', 'error']}
          onLogLevelChange={vi.fn()}
          collectionsPerPage={20}
          videosPerPage={20}
          onCollectionsPerPageChange={vi.fn()}
          onVideosPerPageChange={vi.fn()}
          activityBarPosition="left"
          onActivityBarPositionChange={vi.fn()}
          theme="dawn"
          onThemeChange={vi.fn()}
          cacheMb={0}
          cacheCleaning={false}
          onCleanCache={vi.fn()}
          onOpenLogs={vi.fn()}
        />
        <LoginModal onClose={onCloseLogin} onSuccess={vi.fn()} initialPlatform="douyin" />
      </I18nProvider>,
    );

    await waitFor(() => {
      expect(screen.getByText(TRANSLATIONS.en.settingsTitle)).toBeTruthy();
      expect(screen.getAllByRole('dialog')).toHaveLength(2);
    });

    fireEvent.keyDown(document, { key: 'Escape' });

    await waitFor(() => {
      expect(onCloseLogin).toHaveBeenCalledTimes(1);
    });
    expect(onCloseSettings).not.toHaveBeenCalled();
  });
});
