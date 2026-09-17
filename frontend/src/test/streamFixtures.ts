import { vi } from 'vitest';

/**
 * Push-based control handle for a fake streaming HTTP response body, so a
 * test can construct adversarial chunk boundaries against the REAL
 * ReadableStream + TextDecoderStream pipeline `chatAskStream` uses.
 */
export interface SseStreamController {
  /**
   * Enqueue one `fetch` stream chunk. Strings are UTF-8-encoded — callers
   * needing to split a multi-byte character across chunks should pass
   * pre-sliced Uint8Array chunks instead.
   */
  push(chunk: string | Uint8Array): void;
  /** Normal end-of-stream (next `reader.read()` resolves `{done: true}`). */
  close(): void;
  /** Stream errors (next `reader.read()` rejects with `err`). */
  error(err: unknown): void;
}

export function createSseStream(status = 200): { response: Response; controller: SseStreamController } {
  const encoder = new TextEncoder();
  let ctrl!: ReadableStreamDefaultController<Uint8Array>;
  const body = new ReadableStream<Uint8Array>({
    start: c => { ctrl = c; },
  });
  const response = new Response(body, { status });
  return {
    response,
    controller: {
      push: chunk => ctrl.enqueue(typeof chunk === 'string' ? encoder.encode(chunk) : chunk),
      close: () => ctrl.close(),
      error: err => ctrl.error(err),
    },
  };
}

/**
 * Stubs global `fetch` to resolve once with `response`. Returns the spy so
 * tests can assert on the request `chatAskStream` actually sent (URL, body,
 * and — critically — that the AbortSignal was forwarded).
 */
export function stubFetchResolve(response: Response) {
  const fetchMock = vi.fn().mockResolvedValue(response);
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

export function stubFetchReject(err: unknown) {
  const fetchMock = vi.fn().mockRejectedValue(err);
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}
