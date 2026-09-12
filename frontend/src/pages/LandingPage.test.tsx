import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

import { I18nProvider } from '../i18n';
import LandingPage from './LandingPage';


describe('LandingPage login intent', () => {
  it('prefetches the login modal only after login-button intent', () => {
    const onStartLogin = vi.fn();
    const onLoginIntent = vi.fn();
    render(
      <I18nProvider>
        <LandingPage onStartLogin={onStartLogin} onLoginIntent={onLoginIntent} busy={false} />
      </I18nProvider>,
    );

    const buttons = screen.getAllByRole('button');
    fireEvent.pointerEnter(buttons[0]);
    fireEvent.focus(buttons[1]);

    expect(onLoginIntent).toHaveBeenCalledTimes(2);
    expect(onStartLogin).not.toHaveBeenCalled();
  });
});
