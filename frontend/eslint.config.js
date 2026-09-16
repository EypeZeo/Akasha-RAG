import { defineConfig, globalIgnores } from 'eslint/config';
import js from '@eslint/js';
import globals from 'globals';
import tseslint from 'typescript-eslint';
import reactHooks from 'eslint-plugin-react-hooks';
import reactRefresh from 'eslint-plugin-react-refresh';

export default defineConfig(
  globalIgnores(['dist/**']),

  // Global: report stale eslint-disable comments as errors (default is
  // warn) — this repo already has 5 hand-written exhaustive-deps
  // suppressions; if the code around one changes and the comment is no
  // longer needed, CI should fail, not leave an easy-to-ignore warning.
  {
    linterOptions: { reportUnusedDisableDirectives: 'error' },
  },

  // App source: React + TypeScript, browser runtime.
  {
    files: ['src/**/*.{ts,tsx}'],
    extends: [
      js.configs.recommended,
      tseslint.configs.recommended, // non-type-checked, see rationale below
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      ecmaVersion: 2020, // matches tsconfig.json's `target`
      sourceType: 'module',
      globals: globals.browser,
    },
    plugins: { 'react-hooks': reactHooks },
    rules: {
      // tsc -b (already gating `npm run build`) checks this, and does it
      // correctly for TS-only constructs (ambient globals, declaration
      // merging) that core no-undef doesn't understand.
      // https://typescript-eslint.io/troubleshooting/faqs/eslint/
      'no-undef': 'off',

      // Hand-picked, not the plugin's `recommended` preset: v7.1.1's
      // recommended bundles ~14 additional "React Compiler" rules
      // (purity/immutability/refs/set-state-in-render/etc.) this codebase
      // has never been checked against. Revisit as its own follow-up.
      'react-hooks/rules-of-hooks': 'error',
      'react-hooks/exhaustive-deps': 'error',

      // `catch { /* ignore */ }` and bare `catch {}` are an established,
      // deliberate pattern here (8 bare sites + ~30 commented sites as of
      // 2026-09). allowEmptyCatch makes both forms legal with zero code
      // changes required.
      'no-empty': ['error', { allowEmptyCatch: true }],

      // Pre-authorized escape hatch for intentionally-unused bindings
      // (event-handler params kept for signature shape, destructured
      // fields kept for shape, etc.) so sites don't need dead-code
      // deletion or one-off inline disables. Must be opted into by name,
      // so it can't silently hide a genuine accidental-unused mistake.
      '@typescript-eslint/no-unused-vars': ['error', {
        argsIgnorePattern: '^_',
        varsIgnorePattern: '^_',
        caughtErrorsIgnorePattern: '^_',
      }],

      // 41 pre-existing, unreviewed `any` sites across 15 files (29 src +
      // 12 test). Visible but non-blocking until each is reviewed
      // individually; tighten to 'error' file-by-file as a separate,
      // later effort. Do not silently turn this off. lint:ci caps the
      // total warning count so new `any` sites can't accumulate silently.
      '@typescript-eslint/no-explicit-any': 'warn',
    },
  },

  // vite.config.ts + vitest.config.ts: real committed source, but Node
  // runtime, no React/JSX. Both verified to have no empty catch blocks —
  // no-empty stays at its default strict setting here (no
  // allowEmptyCatch); a build/test config swallowing an exception is more
  // worth flagging than application code doing the same.
  {
    files: ['vite.config.ts', 'vitest.config.ts'],
    extends: [js.configs.recommended, tseslint.configs.recommended],
    languageOptions: { ecmaVersion: 2020, sourceType: 'module', globals: globals.node },
    rules: { 'no-undef': 'off' },
  },

  // Node build/check scripts: plain JS, no TypeScript, no React.
  {
    files: ['scripts/**/*.mjs'],
    extends: [js.configs.recommended],
    languageOptions: { ecmaVersion: 2022, sourceType: 'module', globals: globals.node },
  },
);
