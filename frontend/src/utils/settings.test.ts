import { describe, it, expect, beforeEach } from 'vitest';
import { act, render } from '@testing-library/react';
import { createElement } from 'react';
import {
  DEFAULT_SOURCES_PANEL_WIDTH,
  readSetting,
  readSourcesPanelWidth,
  useThemeSetting,
  writeSetting,
  writeSourcesPanelWidth,
} from './settings';

const OPTS = [5, 10, 20, 0] as const;

describe('readSetting', () => {
  beforeEach(() => localStorage.clear());

  it('returns the fallback when nothing is stored', () => {
    expect(readSetting('ui.x', OPTS, 10)).toBe(10);
  });

  it('reads a valid stored value under the akasha: prefix', () => {
    writeSetting('ui.x', 20);
    expect(readSetting('ui.x', OPTS, 10)).toBe(20);
  });

  it('falls back for a value that is not in the whitelist', () => {
    localStorage.setItem('akasha:ui.x', '999');
    expect(readSetting('ui.x', OPTS, 10)).toBe(10);
  });

  it('falls back for a non-numeric value', () => {
    localStorage.setItem('akasha:ui.x', 'abc');
    expect(readSetting('ui.x', OPTS, 10)).toBe(10);
  });

  it('supports the 0 = "all" sentinel', () => {
    writeSetting('ui.x', 0);
    expect(readSetting('ui.x', OPTS, 10)).toBe(0);
  });

  it('works with string whitelists too', () => {
    const positions = ['left', 'top'] as const;
    writeSetting('ui.pos', 'top');
    expect(readSetting('ui.pos', positions, 'left')).toBe('top');
    localStorage.setItem('akasha:ui.pos', 'diagonal');
    expect(readSetting('ui.pos', positions, 'left')).toBe('left');
  });
});

describe('useThemeSetting', () => {
  beforeEach(() => {
    localStorage.clear();
    delete document.documentElement.dataset.theme;
  });

  it('applies and persists a whitelisted theme on the document root', () => {
    let chooseTheme: ((theme: 'dawn' | 'midnight' | 'ocean' | 'forest') => void) | undefined;

    function Probe() {
      const [, setTheme] = useThemeSetting();
      chooseTheme = setTheme;
      return null;
    }

    render(createElement(Probe));
    expect(document.documentElement.dataset.theme).toBe('dawn');

    act(() => chooseTheme?.('midnight'));
    expect(document.documentElement.dataset.theme).toBe('midnight');
    expect(localStorage.getItem('akasha:ui.theme')).toBe('midnight');
  });
});

describe('Sources panel width setting', () => {
  beforeEach(() => localStorage.clear());

  it('persists a valid dragged width and rejects malformed or out-of-range values', () => {
    writeSourcesPanelWidth(417.4);
    expect(readSourcesPanelWidth()).toBe(417);

    localStorage.setItem('akasha:ui.sourcesPanelWidth', '999');
    expect(readSourcesPanelWidth()).toBe(DEFAULT_SOURCES_PANEL_WIDTH);

    localStorage.setItem('akasha:ui.sourcesPanelWidth', 'not-a-number');
    expect(readSourcesPanelWidth()).toBe(DEFAULT_SOURCES_PANEL_WIDTH);
  });
});
