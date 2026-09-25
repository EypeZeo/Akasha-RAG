import { afterEach, describe, expect, it, vi } from 'vitest';
import { listPendingKnowledge } from './api';

afterEach(() => {
  vi.unstubAllGlobals();
});

function stubJson(body: unknown) {
  const fetchMock = vi.fn().mockImplementation(async () => new Response(JSON.stringify(body), { status: 200 }));
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

describe('listPendingKnowledge', () => {
  it('forwards the abort signal to fetch', async () => {
    const fetchMock = stubJson({ success: true, items: [] });
    const controller = new AbortController();

    await listPendingKnowledge('c1', 'video', 2, 50, 'douyin', controller.signal);

    expect(fetchMock.mock.calls[0][1].signal).toBe(controller.signal);
  });

  it('builds the scope, page and platform into the query, and sends no signal when none is given', async () => {
    const fetchMock = stubJson({ success: true, items: [] });

    await listPendingKnowledge('c1', 'video', 2, 50, 'douyin');

    expect(String(fetchMock.mock.calls[0][0]))
      .toMatch(/\/knowledge\/pending\?collection_id=c1&content_type=video&platform=douyin&page=2&page_size=50$/);
    expect(fetchMock.mock.calls[0][1].signal).toBeUndefined();
  });

  it('leaves the "all" filters out of the query', async () => {
    const fetchMock = stubJson({ success: true, items: [] });

    await listPendingKnowledge('all', 'all', 1, 50, 'all');

    expect(String(fetchMock.mock.calls[0][0])).toMatch(/\/knowledge\/pending\?page=1&page_size=50$/);
  });
});
