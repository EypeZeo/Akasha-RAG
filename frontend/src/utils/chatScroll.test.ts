import { describe, it, expect } from 'vitest';
import { distanceFromBottom, isNearBottom, STICK_TO_BOTTOM_PX } from './chatScroll';

describe('chatScroll', () => {
  it('distanceFromBottom = scrollHeight - scrollTop - clientHeight', () => {
    expect(distanceFromBottom({ scrollTop: 0, scrollHeight: 1000, clientHeight: 400 })).toBe(600);
    expect(distanceFromBottom({ scrollTop: 600, scrollHeight: 1000, clientHeight: 400 })).toBe(0);
  });

  it('isNearBottom is true when within the threshold', () => {
    expect(isNearBottom({ scrollTop: 500, scrollHeight: 1000, clientHeight: 400 })).toBe(true); // 100 < 120
    expect(isNearBottom({ scrollTop: 600, scrollHeight: 1000, clientHeight: 400 })).toBe(true); // exactly at bottom
  });

  it('isNearBottom is false when the user has scrolled up to read history', () => {
    expect(isNearBottom({ scrollTop: 0, scrollHeight: 5000, clientHeight: 500 })).toBe(false);
    expect(isNearBottom({ scrollTop: 4000, scrollHeight: 5000, clientHeight: 500 })).toBe(false); // 500 > 120
  });

  it('respects a custom threshold', () => {
    const m = { scrollTop: 4700, scrollHeight: 5000, clientHeight: 200 }; // distance 100
    expect(isNearBottom(m, 50)).toBe(false);
    expect(isNearBottom(m, 150)).toBe(true);
  });

  it('exposes the default threshold', () => {
    expect(STICK_TO_BOTTOM_PX).toBe(120);
  });
});
