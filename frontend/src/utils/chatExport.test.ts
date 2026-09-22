import { describe, expect, it, vi } from 'vitest';
import { buildChatWordDocument, exportChatToMarkdown, exportChatToText, type ExportMessage } from './chatExport';

const labels = {
  locale: 'en', heading: 'Heading', topic: 'Topic', exportedAt: 'At', messageCount: 'Count',
  you: 'You', assistant: 'AI', system: 'System', latency: 'Latency', sources: 'Sources', match: 'Match', footer: 'Footer',
};

describe('chat export artifacts', () => {
  it('Word includes system messages and escapes the document title', () => {
    const text = buildChatWordDocument([{ role: 'system', content: '<failure>' }], '<unsafe>', labels);
    expect(text).toContain('System</b>: &lt;failure&gt;');
    expect(text).toContain('<title>&lt;unsafe&gt;</title>');
  });
});

/** Captures the Blob handed to a real browser download so the generated text can be inspected.
 *  jsdom has no createObjectURL/revokeObjectURL implementation, so these are assigned directly
 *  (not vi.spyOn, which requires the method to already exist) and restored by hand. */
async function capturedDownload(run: () => void): Promise<string> {
  let blob: Blob | undefined;
  const createCalls: unknown[] = [];
  const originalCreate = URL.createObjectURL;
  const originalRevoke = URL.revokeObjectURL;
  URL.createObjectURL = ((b: Blob) => { blob = b; createCalls.push(b); return 'blob:captured'; }) as typeof URL.createObjectURL;
  URL.revokeObjectURL = (() => {}) as typeof URL.revokeObjectURL;
  const clickSpy = vi.fn();
  const originalCreateElement = document.createElement.bind(document);
  const createElementSpy = vi.spyOn(document, 'createElement').mockImplementation((tag: string) => {
    const el = originalCreateElement(tag);
    if (tag === 'a') (el as HTMLAnchorElement).click = clickSpy;
    return el;
  });

  try {
    run();
    expect(clickSpy).toHaveBeenCalledTimes(1);
    expect(createCalls).toHaveLength(1);
    return await blob!.text();
  } finally {
    URL.createObjectURL = originalCreate;
    URL.revokeObjectURL = originalRevoke;
    createElementSpy.mockRestore();
  }
}

describe('chat export link safety (Markdown, Text)', () => {
  const withSource = (url: string): ExportMessage[] => [
    { role: 'assistant', content: 'answer', sources: [{ title: 'Source', url }] },
  ];

  it('keeps an http(s) source link and drops anything else in the Markdown export', async () => {
    const safe = await capturedDownload(() => exportChatToMarkdown(withSource('https://example.test/a'), 't', labels));
    expect(safe).toContain('[Source](https://example.test/a)');

    const unsafe = await capturedDownload(() => exportChatToMarkdown(withSource('javascript:alert(1)'), 't', labels));
    expect(unsafe).toContain('[Source](#)');
    expect(unsafe).not.toContain('javascript:');
  });

  it('keeps an http(s) source link and drops anything else in the Text export', async () => {
    const safe = await capturedDownload(() => exportChatToText(withSource('http://example.test/b'), 't', labels));
    expect(safe).toContain('Source (http://example.test/b)');

    const unsafe = await capturedDownload(() => exportChatToText(withSource('data:text/html,<b>x</b>'), 't', labels));
    expect(unsafe).toContain('Source (#)');
    expect(unsafe).not.toContain('data:text/html');
  });
});
