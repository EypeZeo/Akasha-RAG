import { describe, it, expect, afterEach, vi } from 'vitest';
import { chatAskStream } from './api';
import { createSseStream, stubFetchResolve, stubFetchReject } from './test/streamFixtures';

afterEach(() => {
  vi.unstubAllGlobals();
});

interface StreamEvent {
  _event: string;
  [key: string]: unknown;
}

async function collect(gen: AsyncGenerator<StreamEvent>): Promise<StreamEvent[]> {
  const out: StreamEvent[] = [];
  for await (const item of gen) out.push(item);
  return out;
}

function concatBytes(...parts: Uint8Array[]): Uint8Array {
  const total = parts.reduce((n, p) => n + p.length, 0);
  const out = new Uint8Array(total);
  let offset = 0;
  for (const p of parts) {
    out.set(p, offset);
    offset += p.length;
  }
  return out;
}

describe('chatAskStream', () => {
  it('1. yields a single delta event from one full SSE line in one chunk', async () => {
    const { response, controller } = createSseStream();
    stubFetchResolve(response);
    const resultPromise = collect(chatAskStream('q'));
    controller.push('event: delta\ndata: {"text":"hi"}\n');
    controller.close();
    expect(await resultPromise).toEqual([{ text: 'hi', _event: 'delta' }]);
  });

  it('2. yields multiple data lines pushed in a single chunk, in order', async () => {
    const { response, controller } = createSseStream();
    stubFetchResolve(response);
    const resultPromise = collect(chatAskStream('q'));
    controller.push('data: {"a":1}\ndata: {"a":2}\n');
    controller.close();
    expect(await resultPromise).toEqual([
      { a: 1, _event: '' },
      { a: 2, _event: '' },
    ]);
  });

  it('3. reassembles an SSE data line split across two chunks', async () => {
    const { response, controller } = createSseStream();
    stubFetchResolve(response);
    const resultPromise = collect(chatAskStream('q'));
    controller.push('data: {"tex');
    controller.push('t":"ab"}\n');
    controller.close();
    expect(await resultPromise).toEqual([{ text: 'ab', _event: '' }]);
  });

  it('4. correctly decodes a UTF-8 multi-byte character split across a chunk boundary inside a data line', async () => {
    const { response, controller } = createSseStream();
    stubFetchResolve(response);
    const resultPromise = collect(chatAskStream('q'));
    const encoder = new TextEncoder();
    const prefix = encoder.encode('data: {"text":"');
    const charBytes = encoder.encode('中'); // 3-byte UTF-8 sequence
    const suffix = encoder.encode('"}\n');
    // Split mid-character: first chunk ends 1 byte into the 3-byte sequence.
    controller.push(concatBytes(prefix, charBytes.slice(0, 1)));
    controller.push(concatBytes(charBytes.slice(1), suffix));
    controller.close();
    expect(await resultPromise).toEqual([{ text: '中', _event: '' }]);
  });

  it('5. attaches the event type to its data line even when the event: line arrives in an earlier chunk', async () => {
    const { response, controller } = createSseStream();
    stubFetchResolve(response);
    const resultPromise = collect(chatAskStream('q'));
    controller.push('event: meta\n');
    controller.push('data: {"session_id":1}\n');
    controller.close();
    expect(await resultPromise).toEqual([{ session_id: 1, _event: 'meta' }]);
  });

  it('6. falls back to a raw payload when a data line is not valid JSON, without throwing', async () => {
    const { response, controller } = createSseStream();
    stubFetchResolve(response);
    const resultPromise = collect(chatAskStream('q'));
    controller.push('data: {not json\n');
    controller.close();
    expect(await resultPromise).toEqual([{ raw: '{not json', _event: '' }]);
  });

  it('7. parses a final chunk that has no trailing newline, on stream close', async () => {
    const { response, controller } = createSseStream();
    stubFetchResolve(response);
    const resultPromise = collect(chatAskStream('q'));
    controller.push('data: {"text":"tail"}'); // no trailing \n
    controller.close();
    expect(await resultPromise).toEqual([{ text: 'tail', _event: '' }]);
  });

  it('8. strips a trailing \\r so CRLF line endings parse the same as LF', async () => {
    const { response, controller } = createSseStream();
    stubFetchResolve(response);
    const resultPromise = collect(chatAskStream('q'));
    controller.push('data: {"text":"x"}\r\n');
    controller.close();
    expect(await resultPromise).toEqual([{ text: 'x', _event: '' }]);
  });

  it('9. throws before any chunk is read when the HTTP response is not ok', async () => {
    const { response } = createSseStream(500);
    stubFetchResolve(response);
    await expect(chatAskStream('q').next()).rejects.toThrow('HTTP 500');
  });

  it('10. throws when the response has no body', async () => {
    stubFetchResolve(new Response(null, { status: 200 }));
    await expect(chatAskStream('q').next()).rejects.toThrow('No stream body');
  });

  it('11. propagates an AbortError from fetch uncaught, with no special handling in api.ts', async () => {
    stubFetchReject(new DOMException('aborted', 'AbortError'));
    await expect(chatAskStream('q').next()).rejects.toMatchObject({ name: 'AbortError' });
  });

  it('12. forwards the AbortSignal into the underlying fetch call', async () => {
    const { response, controller } = createSseStream();
    const fetchMock = stubFetchResolve(response);
    const ac = new AbortController();
    const resultPromise = collect(chatAskStream('q', null, null, ac.signal));
    controller.push('data: {"a":1}\n');
    controller.close();
    await resultPromise;
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][1].signal).toBe(ac.signal);
  });

  it('13. the event type resets after being consumed by its data line', async () => {
    const { response, controller } = createSseStream();
    stubFetchResolve(response);
    const resultPromise = collect(chatAskStream('q'));
    controller.push('event: meta\ndata: {"a":1}\ndata: {"a":2}\n');
    controller.close();
    expect(await resultPromise).toEqual([
      { a: 1, _event: 'meta' },
      { a: 2, _event: '' },
    ]);
  });
});
