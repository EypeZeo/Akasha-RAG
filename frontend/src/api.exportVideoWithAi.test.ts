import { afterEach, describe, expect, it, vi } from 'vitest';
import { exportVideoWithAi } from './api';

afterEach(() => vi.unstubAllGlobals());

describe('AI single-item export', () => {
  it('sends the CSRF client header and preserves a Unicode download filename', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response('# Summary', {
      headers: {
        'content-type': 'text/markdown',
        'content-disposition': `attachment; filename*=utf-8''${encodeURIComponent('知乎摘要.md')}`,
      },
    }));
    vi.stubGlobal('fetch', fetchMock);
    const result = await exportVideoWithAi('id/2', 'zhihu');
    expect(fetchMock).toHaveBeenCalledWith('/api/knowledge/export/id%2F2?mode=ai&platform=zhihu', {
      headers: { 'X-Akasha-Client': '1' },
    });
    expect(result.filename).toBe('知乎摘要.md');
    expect(await result.blob.text()).toBe('# Summary');
  });

  it('does not download JSON failure responses or HTTP failures as documents', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{"success":false}', {
      headers: { 'content-type': 'application/json' },
    })));
    await expect(exportVideoWithAi('id', 'zhihu')).rejects.toThrow('Export did not return a document');
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(null, { status: 403 })));
    await expect(exportVideoWithAi('id', 'zhihu')).rejects.toThrow('HTTP 403');
  });
});
