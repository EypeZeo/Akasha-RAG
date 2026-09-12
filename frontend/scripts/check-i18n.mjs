#!/usr/bin/env node
/**
 * i18n keyset guard.
 *
 * Fails (non-zero exit) if the per-language dictionaries inside
 * `src/i18n.tsx` `TRANSLATIONS` do not all expose exactly the same keys.
 * `t()` silently falls back to zh then to the raw key, so a missing key
 * ships as Chinese / a literal id with no build error — this catches it.
 *
 * The file is TSX and cannot be imported by Node, so we scan it with a
 * small string-aware brace walker instead of a JS parser.
 */
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const SRC_DIR = fileURLToPath(new URL('../src/', import.meta.url));
const I18N_FILE = SRC_DIR + 'i18n.tsx';
const text = readFileSync(I18N_FILE, 'utf8');

const anchor = text.indexOf('TRANSLATIONS');
if (anchor === -1) {
  console.error('check-i18n: could not find TRANSLATIONS in src/i18n.tsx');
  process.exit(2);
}
const objStart = text.indexOf('{', anchor);
if (objStart === -1) {
  console.error('check-i18n: could not find TRANSLATIONS object literal');
  process.exit(2);
}

// Walk the object literal, tracking string state so braces / colons inside
// string values (e.g. "{count}") are ignored. Record identifiers that are
// immediately followed by ':' at depth 1 (languages) and depth 2 (keys).
const langKeys = new Map(); // lang -> Set(keys)
let depth = 0;
let i = objStart;
let currentLang = null;
let str = null; // active quote char or null
let pendingIdent = '';

for (; i < text.length; i++) {
  const ch = text[i];

  if (str) {
    if (ch === '\\') { i++; continue; }
    if (ch === str) str = null;
    continue;
  }

  if (ch === '"' || ch === "'" || ch === '`') { str = ch; pendingIdent = ''; continue; }

  if (ch === '{') { depth++; pendingIdent = ''; continue; }
  if (ch === '}') {
    depth--;
    pendingIdent = '';
    if (depth === 0) break; // end of TRANSLATIONS
    if (depth === 1) currentLang = null; // left a language block
    continue;
  }

  if (ch === ':') {
    const name = pendingIdent.trim();
    pendingIdent = '';
    if (name && /^[A-Za-z_$][\w$]*$/.test(name)) {
      if (depth === 1) {
        currentLang = name;
        if (!langKeys.has(currentLang)) langKeys.set(currentLang, new Set());
      } else if (depth === 2 && currentLang) {
        langKeys.get(currentLang).add(name);
      }
    }
    continue;
  }

  if (ch === ',' || ch === '\n') { pendingIdent = ''; continue; }
  pendingIdent += ch;
}

const langs = [...langKeys.keys()];
if (langs.length < 2) {
  console.error(`check-i18n: expected multiple language dicts, found ${langs.length}`);
  process.exit(2);
}

// zh is the source of truth (t() falls back to it).
const reference = langKeys.get('zh') ?? langKeys.get(langs[0]);
let failed = false;

for (const lang of langs) {
  const keys = langKeys.get(lang);
  const missing = [...reference].filter(k => !keys.has(k));
  const extra = [...keys].filter(k => !reference.has(k));
  if (missing.length || extra.length) {
    failed = true;
    console.error(`\n[${lang}] keyset differs from zh:`);
    if (missing.length) console.error(`  missing (${missing.length}): ${missing.join(', ')}`);
    if (extra.length) console.error(`  extra   (${extra.length}): ${extra.join(', ')}`);
  }
}

// --- used-but-undefined check ---------------------------------------------
// Any t('literal') referenced in the app must exist in the dictionaries.
// Dynamic keys (t(`route_${x}`)) are skipped — they cannot be checked statically.
function walk(dir) {
  const out = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = dir + entry.name;
    if (entry.isDirectory()) out.push(...walk(full + '/'));
    else if (/\.(ts|tsx)$/.test(entry.name) && full !== I18N_FILE) out.push(full);
  }
  return out;
}

const USED = /(?<![\w.])t\(\s*(['"])([A-Za-z_$][\w$]*)\1/g;
const usedKeys = new Set();
for (const file of walk(SRC_DIR)) {
  const body = readFileSync(file, 'utf8');
  for (const m of body.matchAll(USED)) usedKeys.add(m[2]);
}
const undefinedUsed = [...usedKeys].filter(k => !reference.has(k)).sort();
if (undefinedUsed.length) {
  failed = true;
  console.error(`\n[used-but-undefined] ${undefinedUsed.length} key(s) referenced in code but missing from every dict:`);
  console.error(`  ${undefinedUsed.join(', ')}`);
}

if (failed) {
  console.error('\ncheck-i18n: FAILED — every language dict must expose the same keys, and every t() key must be defined.\n');
  process.exit(1);
}

console.log(
  `check-i18n: OK — ${langs.length} languages, ${reference.size} keys each; ` +
  `${usedKeys.size} static t() keys all defined (${langs.join(', ')}).`,
);
