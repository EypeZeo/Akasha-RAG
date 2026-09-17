import { describe, it, expect } from 'vitest';

describe('streaming environment smoke test', () => {
  it('TextDecoderStream, ReadableStream, and fetch are real globals under jsdom', () => {
    expect(typeof TextDecoderStream).toBe('function');
    expect(typeof ReadableStream).toBe('function');
    expect(typeof fetch).toBe('function');
  });

  it('a ReadableStream<Uint8Array> piped through TextDecoderStream round-trips a UTF-8 string split across chunk boundaries', async () => {
    const bytes = new TextEncoder().encode('héllo 中文');
    const mid = 3; // deliberately split inside a multi-byte sequence
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(bytes.slice(0, mid));
        controller.enqueue(bytes.slice(mid));
        controller.close();
      },
    });
    const reader = body.pipeThrough(new TextDecoderStream()).getReader();
    let out = '';
    for (let r = await reader.read(); !r.done; r = await reader.read()) out += r.value;
    expect(out).toBe('héllo 中文');
  });
});
