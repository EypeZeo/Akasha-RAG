import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import ActivityBar from './ActivityBar';
import { I18nProvider } from '../i18n';

function setup(position: 'left' | 'top' = 'left') {
  const onTabChange = vi.fn();
  render(
    <I18nProvider>
      <ActivityBar
        position={position}
        activeTab="sources"
        onTabChange={onTabChange}
        onOpenSettings={vi.fn()}
        loggedPlatforms={[]}
      />
    </I18nProvider>,
  );
  return { onTabChange };
}

describe('ActivityBar', () => {
  it('exposes a tablist with two tabs and correct aria-selected', () => {
    setup();
    const tabs = screen.getAllByRole('tab');
    expect(tabs).toHaveLength(2);
    expect(tabs[0]).toHaveAttribute('aria-selected', 'true');
    expect(tabs[1]).toHaveAttribute('aria-selected', 'false');
    expect(tabs[0]).toHaveAttribute('aria-controls', 'workspace-pane-sources');
  });

  it('ArrowDown moves to the next tab when vertical', async () => {
    const { onTabChange } = setup('left');
    const tabs = screen.getAllByRole('tab');
    tabs[0].focus();
    await userEvent.keyboard('{ArrowDown}');
    expect(onTabChange).toHaveBeenCalledWith('chat');
  });

  it('ArrowRight moves to the next tab when horizontal', async () => {
    const { onTabChange } = setup('top');
    const tabs = screen.getAllByRole('tab');
    tabs[0].focus();
    await userEvent.keyboard('{ArrowRight}');
    expect(onTabChange).toHaveBeenCalledWith('chat');
  });

  it('End jumps to the last tab', async () => {
    const { onTabChange } = setup('left');
    screen.getAllByRole('tab')[0].focus();
    await userEvent.keyboard('{End}');
    expect(onTabChange).toHaveBeenCalledWith('chat');
  });

  it('clicking a tab reports the change', async () => {
    const { onTabChange } = setup();
    await userEvent.click(screen.getAllByRole('tab')[1]);
    expect(onTabChange).toHaveBeenCalledWith('chat');
  });
});
