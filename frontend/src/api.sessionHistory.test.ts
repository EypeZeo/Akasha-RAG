import { afterEach, describe, expect, it, vi } from 'vitest';
import { chatAskStream, getSessionMessages, getSessionSnapshot } from './api';
import { createSseStream, stubFetchResolve } from './test/streamFixtures';

afterEach(() => {
  vi.unstubAllGlobals();
});

function stubJson(body: unknown, status = 200) {
  const fetchMock = vi.fn().mockImplementation(async () => new Response(typeof body === 'string' ? body : JSON.stringify(body), { status }));
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

describe('session history requests', () => {
  it('builds the messages query from before, limit and until, and forwards the abort signal', async () => {
    const fetchMock = stubJson({ success: true, items: [] });
    const controller = new AbortController();

    await getSessionMessages(7, { before: 30, limit: 200, until: 99, signal: controller.signal });

    expect(String(fetchMock.mock.calls[0][0])).toMatch(/\/chat\/sessions\/7\/messages\?before=30&limit=200&until=99$/);
    expect(fetchMock.mock.calls[0][1].signal).toBe(controller.signal);
  });

  it('sends no query string when there are no options, and keeps a zero cursor', async () => {
    const fetchMock = stubJson({ success: true, items: [] });

    await getSessionMessages(7);
    await getSessionMessages(7, { until: 0 });

    expect(String(fetchMock.mock.calls[0][0])).toMatch(/\/chat\/sessions\/7\/messages$/);
    expect(String(fetchMock.mock.calls[1][0])).toMatch(/\/chat\/sessions\/7\/messages\?until=0$/);
  });

  it('asks for the snapshot of one session, forwarding the abort signal', async () => {
    const fetchMock = stubJson({ success: true, session_id: 7, snapshot_id: 32, total: 4 });
    const controller = new AbortController();

    const snapshot = await getSessionSnapshot(7, controller.signal);

    expect(snapshot).toEqual({ success: true, session_id: 7, snapshot_id: 32, total: 4 });
    expect(String(fetchMock.mock.calls[0][0])).toMatch(/\/chat\/sessions\/7\/snapshot$/);
    expect(fetchMock.mock.calls[0][1].signal).toBe(controller.signal);
  });

  it('turns an HTTP 413 into an error that starts with "413:", which is how the export recognises "too large"', async () => {
    stubJson('History export exceeds the message limit', 413);

    await expect(getSessionSnapshot(7)).rejects.toThrow(/^413: /);
    await expect(getSessionMessages(7)).rejects.toThrow(/^413: /);
  });
});

describe('chatAskStream client keys', () => {
  async function drain(...args: Parameters<typeof chatAskStream>) {
    const { response, controller } = createSseStream();
    const fetchMock = stubFetchResolve(response);
    const run = (async () => { for await (const _ of chatAskStream(...args)) { /* consume */ } })();
    controller.close();
    await run;
    return JSON.parse(fetchMock.mock.calls[0][1].body as string) as Record<string, unknown>;
  }

  it('sends the keys of the exchange in the request body', async () => {
    const body = await drain('q', 7, 'col', 'douyin', undefined, { user: 'u-1', assistant: 'a-1' });

    expect(body).toEqual({
      query: 'q', session_id: 7, collection_id: 'col', platform: 'douyin', client_keys: { user: 'u-1', assistant: 'a-1' },
    });
  });

  it('leaves the field out when there are no keys', async () => {
    const body = await drain('q');

    expect(body).toEqual({ query: 'q', session_id: null, collection_id: null, platform: null });
  });
});
