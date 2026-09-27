import { afterEach, describe, expect, it, vi } from 'vitest';
import { listCollectionVideos } from './api';

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('listCollectionVideos', () => {
  it('forwards collection, platform, cursor and status filters', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ success: true, items: [] }), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);

    await listCollectionVideos('col-1', 2, 50, 'douyin', 'cursor/2', 'failed');

    expect(String(fetchMock.mock.calls[0][0])).toContain('/favorites/collections/col-1/videos?page=2&size=50');
    expect(String(fetchMock.mock.calls[0][0])).toContain('&platform=douyin&cursor=cursor%2F2&status=failed');
  });

  it('omits the default all-status filter', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ success: true, items: [] }), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);

    await listCollectionVideos('all');

    expect(String(fetchMock.mock.calls[0][0])).toMatch(/\/favorites\/collections\/all\/videos\?page=1&size=20$/);
  });
});
