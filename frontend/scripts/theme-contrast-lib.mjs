export const THEMES = ['dawn', 'midnight', 'ocean', 'forest'];
export const REQUIRED_VARS = ['color-panel-solid', 'color-ink', 'color-ink-soft', 'color-ink-muted', 'color-accent', 'color-on-accent'];

export function luminance(hex) {
  const rgb = [1, 3, 5].map(index => parseInt(hex.slice(index, index + 2), 16) / 255);
  const linear = rgb.map(value => value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4);
  return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2];
}

export function ratio(foreground, background) {
  const [a, b] = [luminance(foreground), luminance(background)].sort((x, y) => y - x);
  return (a + 0.05) / (b + 0.05);
}

/**
 * Evaluate every theme block in `css` independently and return the list of
 * problems found (empty = all good).
 *
 * CI-01: this used to share one `failed` flag across the whole loop, and
 * `if (failed) continue` skipped the REST of the loop's remaining iterations
 * too — so one theme missing a required variable silently hid every
 * subsequent theme's contrast results, not just its own. Each theme here is
 * evaluated independently; a problem in one never suppresses another's.
 */
export function evaluateThemes(css, themes = THEMES, required = REQUIRED_VARS) {
  const problems = [];
  for (const theme of themes) {
    const match = css.match(new RegExp(`:root\\[data-theme=['"]${theme}['"]\\]\\s*\\{([\\s\\S]*?)\\n\\}`));
    if (!match) {
      problems.push(`missing ${theme} theme block`);
      continue;
    }
    const colors = Object.fromEntries([...match[1].matchAll(/--([\w-]+):\s*(#[0-9A-Fa-f]{6})/g)].map(([, name, value]) => [name, value]));

    let themeHasMissingVar = false;
    for (const name of required) {
      if (!colors[name]) {
        problems.push(`${theme} is missing --${name}`);
        themeHasMissingVar = true;
      }
    }
    if (themeHasMissingVar) continue;

    const againstPanel = ['color-ink', 'color-ink-soft', 'color-ink-muted', 'color-accent'];
    for (const color of againstPanel) {
      const value = ratio(colors[color], colors['color-panel-solid']);
      if (value < 4.5) {
        problems.push(`${theme} --${color} contrast is ${value.toFixed(2)}:1 (< 4.5:1)`);
      }
    }
    const onAccent = ratio(colors['color-on-accent'], colors['color-accent']);
    if (onAccent < 4.5) {
      problems.push(`${theme} --color-on-accent contrast is ${onAccent.toFixed(2)}:1 (< 4.5:1)`);
    }
  }
  return problems;
}
