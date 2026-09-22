import { createPortal } from 'react-dom';
import ReactMarkdown, { type Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { Message } from './ChatMessageRow';
import type { ChatExportLabels } from '../utils/chatExport';

export interface PrintSnapshot {
  messages: Message[];
  title: string;
  labels: ChatExportLabels;
  notice?: string;
}

/** A printed link must be an absolute http(s) URL; anything else keeps its text and loses the link. */
function printHref(url: string | undefined | null): string | null {
  if (!url) return null;
  try {
    const { protocol } = new URL(url);
    return protocol === 'http:' || protocol === 'https:' ? url : null;
  } catch {
    return null;
  }
}

// The print document is static: no remote images, no form controls, nothing to click or unfold.
const markdownComponents: Components = {
  a: ({ href, children }) => {
    const target = printHref(href);
    return target ? <a href={target}>{children}</a> : <span>{children}</span>;
  },
  img: ({ alt }) => <span>{alt}</span>,
  input: ({ checked }) => <span>{checked ? '[x] ' : '[ ] '}</span>,
};

const remarkPlugins = [remarkGfm];

/** A separate, bounded document: no virtualizer, collapsed sources or active-tab dependency. */
export default function ChatPrintDocument({ snapshot }: { snapshot: PrintSnapshot }) {
  return createPortal(
    <div className="chat-print-document">
      <h1>{snapshot.title}</h1>
      {snapshot.notice && <p>{snapshot.notice}</p>}
      {snapshot.messages.map(message => (
        <section key={message.clientKey}>
          <h2>{message.role === 'user' ? snapshot.labels.you : message.role === 'assistant' ? snapshot.labels.assistant : snapshot.labels.system}</h2>
          {message.role === 'assistant'
            ? <div className="chat-print-content chat-print-markdown">
              <ReactMarkdown remarkPlugins={remarkPlugins} components={markdownComponents}>{message.content}</ReactMarkdown>
            </div>
            // What the user typed and what the system reported are plain text in the chat too.
            : <div className="chat-print-content chat-print-plain">{message.content}</div>}
          {!!message.sources?.length && <ol>
            {message.sources.map((source, index) => {
              const href = printHref(source.url);
              const label = source.url ? `${source.title} (${source.url})` : source.title;
              return <li key={index}>{href ? <a href={href}>{label}</a> : label}</li>;
            })}
          </ol>}
        </section>
      ))}
    </div>, document.body,
  );
}
