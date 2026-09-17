import '@testing-library/jest-dom/vitest';
import { afterEach, beforeEach } from 'vitest';
import { cleanup } from '@testing-library/react';
// @ts-expect-error -- this frontend project has no @types/node (browser-only
// app); the "node:" specifier still resolves at runtime under Vitest's Node
// process, this is only a missing ambient module declaration.
import { TextDecoderStream as NodeTextDecoderStream } from 'node:stream/web';

// jsdom itself implements neither `TextDecoderStream` nor `ReadableStream`/`fetch`;
// whether they're present as bare globals in a given Vitest+jsdom run depends on
// how Node exposes its own web-stream globals, which can vary across Node
// versions (verified present on local Node 25, but CI pins Node 22 — this repo
// has no local Node 22 toolchain to re-verify against). Rather than assume, this
// unconditionally backfills from Node's own `node:stream/web` module when the
// global is missing — a no-op when it's already there, and behavior-preserving
// when it's not (it's the exact same class Node otherwise exposes as the global).
if (typeof globalThis.TextDecoderStream === 'undefined') {
  (globalThis as unknown as { TextDecoderStream: typeof TextDecoderStream }).TextDecoderStream = NodeTextDecoderStream;
}

// Node 22's experimental localStorage and jsdom's implementation can collide and
// leave `localStorage.clear` undefined. Install a deterministic in-memory shim.
class MemoryStorage {
  private store = new Map<string, string>();
  get length() {
    return this.store.size;
  }
  key(i: number) {
    return [...this.store.keys()][i] ?? null;
  }
  getItem(k: string) {
    return this.store.has(k) ? this.store.get(k)! : null;
  }
  setItem(k: string, v: string) {
    this.store.set(String(k), String(v));
  }
  removeItem(k: string) {
    this.store.delete(k);
  }
  clear() {
    this.store.clear();
  }
}

const mem = new MemoryStorage();
Object.defineProperty(globalThis, 'localStorage', { value: mem, configurable: true });
if (typeof window !== 'undefined') {
  Object.defineProperty(window, 'localStorage', { value: mem, configurable: true });
}

beforeEach(() => localStorage.clear());
afterEach(() => cleanup());
