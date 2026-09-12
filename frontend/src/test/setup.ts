import '@testing-library/jest-dom/vitest';
import { afterEach, beforeEach } from 'vitest';
import { cleanup } from '@testing-library/react';

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
