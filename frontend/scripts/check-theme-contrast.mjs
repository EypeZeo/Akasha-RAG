#!/usr/bin/env node
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { THEMES, evaluateThemes } from './theme-contrast-lib.mjs';

const cssFile = fileURLToPath(new URL('../src/index.css', import.meta.url));
const css = readFileSync(cssFile, 'utf8');
const problems = evaluateThemes(css);

for (const problem of problems) {
  console.error(`check-theme-contrast: ${problem}`);
}
if (problems.length > 0) process.exit(1);
console.log(`check-theme-contrast: OK — ${THEMES.length} themes meet the 4.5:1 text contrast threshold.`);
