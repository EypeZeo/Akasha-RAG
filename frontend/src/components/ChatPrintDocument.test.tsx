import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import ChatPrintDocument from './ChatPrintDocument';
import type { Message } from './ChatMessageRow';

describe('ChatPrintDocument', () => {
  it('renders system messages and expanded source links outside the hidden workspace', () => {
    render(<ChatPrintDocument snapshot={{ title: 'Full history', labels: {
      locale: 'en', heading: 'h', topic: 't', exportedAt: 'at', messageCount: 'n', you: 'You', assistant: 'Assistant', system: 'System', latency: 'l', sources: 's', match: 'm', footer: 'f',
    }, messages: [
      { clientKey: 'system', role: 'system', content: 'failed response' },
      { clientKey: 'answer', role: 'assistant', content: 'answer', sources: [{ platform_item_id: 'source', title: 'Source', url: 'https://example.test/source', score: 0.4 }] },
    ] }} />);
    expect(screen.getByText('failed response')).toBeTruthy();
    const source = screen.getByRole('link', { name: /Source/ });
    expect(source.getAttribute('href')).toBe('https://example.test/source');
    expect(source.closest('.chat-print-document')?.parentElement).toBe(document.body);
  });
});

const labels = {
  locale: 'en', heading: 'h', topic: 't', exportedAt: 'at', messageCount: 'n', you: 'You', assistant: 'Assistant',
  system: 'System', latency: 'l', sources: 's', match: 'm', footer: 'f',
};

function renderDocument(messages: Message[], notice?: string) {
  render(<ChatPrintDocument snapshot={{ title: 'Full history', labels, messages, notice }} />);
  return document.body.querySelector('.chat-print-document') as HTMLElement;
}

const answer = (content: string, sources?: Message['sources']): Message => ({ clientKey: 'a', role: 'assistant', content, sources });
const hrefs = (root: HTMLElement) => [...root.querySelectorAll('a')].map(a => a.getAttribute('href'));

describe('ChatPrintDocument layout', () => {
  it('shows the title, the notice and one heading per role', () => {
    const root = renderDocument([
      { clientKey: 'u', role: 'user', content: 'q' }, answer('a'), { clientKey: 's', role: 'system', content: 'x' },
    ], 'only loaded messages');
    expect(root.querySelector('h1')?.textContent).toBe('Full history');
    expect(root.textContent).toContain('only loaded messages');
    expect([...root.querySelectorAll('h2')].map(h => h.textContent)).toEqual(['You', 'Assistant', 'System']);
  });
});

describe('ChatPrintDocument message content', () => {
  it('keeps the line breaks of a user question, which the chat shows as plain text', () => {
    const root = renderDocument([{ clientKey: 'u', role: 'user', content: 'line one\nline two\n\n- not a list' }]);
    const plain = root.querySelector('.chat-print-plain') as HTMLElement;
    expect(plain.textContent).toBe('line one\nline two\n\n- not a list');
    expect(root.querySelector('ul, li, table')).toBeNull();
  });

  it('renders an assistant answer as GitHub-flavoured markdown: table, lists, code, emphasis', () => {
    const root = renderDocument([answer([
      '## Plan', '', 'Some **bold** and `inline` text.', '',
      '| Name | Score |', '| --- | ---: |', '| alpha | 1 |', '| beta | 2 |', '',
      '1. first', '2. second', '', '- one', '- two', '',
      '```ts', 'const x = 1;', '  indented();', '```',
    ].join('\n'))]);

    expect(root.querySelector('.chat-print-markdown h2')?.textContent).toBe('Plan');
    expect(root.querySelector('strong')?.textContent).toBe('bold');
    const table = root.querySelector('table') as HTMLTableElement;
    expect([...table.querySelectorAll('th')].map(cell => cell.textContent)).toEqual(['Name', 'Score']);
    expect([...table.querySelectorAll('tbody tr')].map(row => [...row.querySelectorAll('td')].map(cell => cell.textContent))).toEqual([
      ['alpha', '1'], ['beta', '2'],
    ]);
    expect([...root.querySelectorAll('ol > li')].map(li => li.textContent)).toEqual(['first', 'second']);
    expect([...root.querySelectorAll('ul > li')].map(li => li.textContent)).toEqual(['one', 'two']);
    expect(root.querySelector('pre > code')?.textContent).toBe('const x = 1;\n  indented();\n');
    expect(root.querySelector('p code')?.textContent).toBe('inline');
    expect(root.textContent).not.toContain('**bold**');
    expect(root.textContent).not.toContain('| --- |');
  });

  it('turns task-list checkboxes into text so the document has no form controls', () => {
    const root = renderDocument([answer('- [x] done\n- [ ] todo')]);
    expect(root.querySelector('input')).toBeNull();
    expect([...root.querySelectorAll('li')].map(li => li.textContent?.replace(/\s+/g, ' ').trim())).toEqual(['[x] done', '[ ] todo']);
  });
});

describe('ChatPrintDocument link safety', () => {
  it('keeps http(s) links and drops the href of anything else, leaving the text', () => {
    const root = renderDocument([answer([
      '[web](https://example.test/page) [plain](http://example.test/)',
      '[script](javascript:alert(1)) [data](data:text/html,<b>x</b>) [vb](vbscript:msgbox(1))',
      '[relative](/settings) [anchor](#top) [mail](mailto:a@example.test)',
    ].join('\n'))]);

    expect(hrefs(root)).toEqual(['https://example.test/page', 'http://example.test/']);
    for (const label of ['script', 'data', 'vb', 'relative', 'anchor', 'mail']) {
      expect(root.textContent).toContain(label);
    }
  });

  it('auto-links a bare https URL but not a bare javascript: one', () => {
    const root = renderDocument([answer('see https://example.test/auto and javascript:alert(1)')]);
    expect(hrefs(root)).toEqual(['https://example.test/auto']);
  });

  it('never loads an image: only the alt text is printed', () => {
    const root = renderDocument([answer('before ![the chart](https://example.test/chart.png) after')]);
    expect(root.querySelector('img')).toBeNull();
    expect(root.textContent).toContain('the chart');
  });

  it('does not turn raw HTML in a message into elements', () => {
    const root = renderDocument([answer('<script>window.hacked = 1</script><img src=x onerror="window.hacked = 2"><b>bold?</b> ok')]);
    expect(root.querySelector('script, img, iframe, b')).toBeNull();
    expect((window as unknown as { hacked?: number }).hacked).toBeUndefined();
    expect(root.textContent).toContain('ok');
  });

  it('gives a source with an unsafe URL no href, and still lists it', () => {
    const root = renderDocument([answer('answer', [
      { platform_item_id: 'a', title: 'Safe', url: 'https://example.test/a' },
      { platform_item_id: 'b', title: 'Unsafe', url: 'javascript:alert(1)' },
      { platform_item_id: 'c', title: 'No url', url: '' },
    ])]);
    expect(hrefs(root)).toEqual(['https://example.test/a']);
    expect([...root.querySelectorAll('ol > li')].map(li => li.textContent)).toEqual([
      'Safe (https://example.test/a)', 'Unsafe (javascript:alert(1))', 'No url',
    ]);
  });
});

describe('ChatPrintDocument is a static document', () => {
  it('contains no interactive or collapsible element, whatever the message holds', () => {
    const root = renderDocument([
      { clientKey: 'u', role: 'user', content: '<button>x</button> [a](https://example.test)' },
      answer([
        '- [x] task', '', '<details><summary>fold</summary>hidden</details>', '',
        '[link](https://example.test) ![img](https://example.test/i.png)', '',
        '| a | b |', '| - | - |', '| 1 | 2 |', '', '```', 'code', '```',
      ].join('\n'), [{ platform_item_id: 's', title: 'Source', url: 'https://example.test/s', score: 0.9 }]),
    ]);
    expect(root.querySelectorAll('button, details, summary, input, textarea, select, iframe, script, [role="button"], [tabindex], [onclick]')).toHaveLength(0);
  });
});
