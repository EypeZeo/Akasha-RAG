import { describe, it, expect, vi, beforeEach } from 'vitest';
import { act, render, screen, waitFor, cleanup, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach } from 'vitest';
import SettingsModal from './SettingsModal';
import LoginModal from './LoginModal';
import { I18nProvider, TRANSLATIONS } from '../i18n';
import * as api from '../api';

vi.mock('../api');

afterEach(cleanup);

function setup(onClose = vi.fn(), accountRefreshKey = 0) {
  const view = render(
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
        accountRefreshKey={accountRefreshKey}
      />
    </I18nProvider>,
  );
  return { onClose, ...view };
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

  it.each([false, true])('refreshes the avatar after login without leaving the account page (late initial response: %s)', async (lateInitialResponse) => {
    let finishOld: (() => void) | undefined;
    if (lateInitialResponse) {
      vi.mocked(api.listPlatforms).mockReturnValueOnce(new Promise(resolve => {
        finishOld = () => resolve({ success: true, platforms: [
          { platform: 'douyin', name: 'Douyin', status: 'idle', is_logged_in: false, nickname: 'Old account' },
        ] });
      }));
    }
    const onClose = vi.fn();
    const view = setup(onClose);
    await userEvent.click(screen.getByRole('button', { name: new RegExp(TRANSLATIONS.en.accountsTitle) }));
    await waitFor(() => expect(api.listPlatforms).toHaveBeenCalled());
    const avatar = 'https://p3-sign.douyinpic.com/tos-cn/profile.webp';
    vi.mocked(api.listPlatforms).mockResolvedValue({ success: true, platforms: [
      { platform: 'douyin', name: 'Douyin', status: 'logged_in', is_logged_in: true, nickname: 'Account owner', avatar_url: avatar },
    ] });
    // Reuse the exact mounted component with the refresh key supplied by Workspace.
    view.rerender(<I18nProvider><SettingsModal
      isOpen onClose={onClose} logLevel="info" availableLevels={['info']} onLogLevelChange={vi.fn()}
      collectionsPerPage={20} videosPerPage={20} onCollectionsPerPageChange={vi.fn()} onVideosPerPageChange={vi.fn()}
      activityBarPosition="left" onActivityBarPositionChange={vi.fn()} theme="dawn" onThemeChange={vi.fn()}
      cacheMb={0} cacheCleaning={false} onCleanCache={vi.fn()} onOpenLogs={vi.fn()} accountRefreshKey={1}
    /></I18nProvider>);
    await waitFor(() => expect(screen.getByText('Account owner')).toBeTruthy());
    const image = document.querySelector(`img[src="${avatar}"]`);
    expect(image).toHaveAttribute('referrerpolicy', 'no-referrer');
    expect(screen.queryByRole('heading', { name: TRANSLATIONS.en.settingsTitle })).toBeNull();
    if (finishOld) await act(async () => { finishOld!(); });
    expect(screen.getByText('Account owner')).toBeTruthy();
    expect(screen.queryByText('Old account')).toBeNull();
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
