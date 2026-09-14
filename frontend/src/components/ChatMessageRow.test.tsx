import { describe, it, expect, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import ChatMessageRow from './ChatMessageRow';
import { I18nProvider, TRANSLATIONS } from '../i18n';
import type { Message } from './ChatMessageRow';

afterEach(cleanup);

function renderRow(msg: Message) {
  return render(
    <I18nProvider>
      <ChatMessageRow msg={msg} animating={false} />
    </I18nProvider>,
  );
}

describe('ChatMessageRow', () => {
  it('renders a plain assistant message', () => {
    const msg: Message = {
      clientKey: 'm1',
      role: 'assistant',
      content: '这是一个普通回复',
    };
    renderRow(msg);
    expect(screen.getByText('这是一个普通回复')).toBeTruthy();
  });

  it('renders a citation badge and the collapsible sources list for a sourced message', () => {
    const msg: Message = {
      clientKey: 'm2',
      role: 'assistant',
      content: '答案内容 [来源: Test Video]',
      sources: [
        { platform_item_id: 'v1', title: 'Test Video', url: 'https://www.douyin.com/video/v1', score: 0.9 },
      ],
    };
    renderRow(msg);
    expect(screen.getByText('[1]')).toBeTruthy();
    expect(screen.getByText(TRANSLATIONS.en.infoAndSources)).toBeTruthy();
    expect(screen.getByText(TRANSLATIONS.en.citationCount.replace('{count}', '1'))).toBeTruthy();
  });

  it('renders a copy button for fenced code blocks via CodeBlock', () => {
    const msg: Message = {
      clientKey: 'm3',
      role: 'assistant',
      content: '```js\nconsole.log(1);\n```',
    };
    renderRow(msg);
    // Two copy buttons render for this message: the header CopyButton (full
    // message) and CodeBlock's own (just the fenced snippet) — both share
    // the same t('copy') title, so assert on the count rather than a single match.
    expect(screen.getAllByTitle(TRANSLATIONS.en.copy)).toHaveLength(2);
  });
});
