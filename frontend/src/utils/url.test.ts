import { describe, it, expect } from 'vitest';
import { safeHref } from './url';

describe('safeHref', () => {
  it('passes http(s) URLs through unchanged', () => {
    expect(safeHref('https://www.bilibili.com/video/BV1x')).toBe('https://www.bilibili.com/video/BV1x');
    expect(safeHref('http://example.com')).toBe('http://example.com');
  });

  it('neutralizes dangerous schemes from scraped source data', () => {
    expect(safeHref('javascript:alert(1)')).toBe('#');
    expect(safeHref('data:text/html,<script>1</script>')).toBe('#');
    expect(safeHref('vbscript:msgbox(1)')).toBe('#');
  });

  it('returns # for empty / invalid input', () => {
    expect(safeHref(undefined)).toBe('#');
    expect(safeHref(null)).toBe('#');
    expect(safeHref('')).toBe('#');
  });
});
