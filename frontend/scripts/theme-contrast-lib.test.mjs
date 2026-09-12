import { describe, it, expect } from 'vitest';
import { evaluateThemes } from './theme-contrast-lib.mjs';

const GOOD_THEME_BLOCK = (name) => `
:root[data-theme='${name}'] {
  --color-panel-solid: #FFFFFF;
  --color-ink: #111111;
  --color-ink-soft: #333333;
  --color-ink-muted: #555555;
  --color-accent: #B33A2E;
  --color-on-accent: #FFFFFF;
}
`;

describe('evaluateThemes (CI-01)', () => {
  it('reports no problems when every theme has valid, high-contrast colors', () => {
    const css = ['a', 'b'].map(GOOD_THEME_BLOCK).join('\n');
    expect(evaluateThemes(css, ['a', 'b'])).toEqual([]);
  });

  it('a missing-variable failure in one theme does not suppress a later theme\'s own problems', () => {
    const broken = GOOD_THEME_BLOCK('a').replace('--color-ink: #111111;\n', '');
    const alsoBroken = GOOD_THEME_BLOCK('b').replace('--color-accent: #B33A2E;', '--color-accent: #FFFFFF;'); // low contrast on white panel
    const css = broken + '\n' + alsoBroken;

    const problems = evaluateThemes(css, ['a', 'b']);

    expect(problems.some(p => p.includes('a is missing --color-ink'))).toBe(true);
    // Theme "b" must still be evaluated and report its own (different) problem,
    // instead of being silently skipped because "a" failed first.
    expect(problems.some(p => p.includes('b --color-accent contrast'))).toBe(true);
  });

  it('flags a theme block that is missing entirely', () => {
    const css = GOOD_THEME_BLOCK('a');
    const problems = evaluateThemes(css, ['a', 'missing-theme']);
    expect(problems).toContain('missing missing-theme theme block');
  });

  it('flags low contrast against the panel background', () => {
    const css = GOOD_THEME_BLOCK('a').replace('--color-ink-muted: #555555;', '--color-ink-muted: #F8F8F8;');
    const problems = evaluateThemes(css, ['a']);
    expect(problems.some(p => p.includes('a --color-ink-muted contrast'))).toBe(true);
  });
});
