/**
 * Akasha-RAG 会话对话导出工具集
 * 支持导出为 Markdown (.md)、Word 文档 (.doc)、PDF 打印流及纯文本 (.txt)
 */

export interface ExportMessage {
  role: 'user' | 'assistant' | 'system';
  content: string;
  sources?: { title: string; url: string; score?: number }[];
  latency_ms?: number;
}

export interface ChatExportLabels {
  locale: string;
  heading: string;
  topic: string;
  exportedAt: string;
  messageCount: string;
  you: string;
  assistant: string;
  system: string;
  latency: string;
  sources: string;
  match: string;
  footer: string;
}

/**
 * 触发浏览器文件下载
 */
export function downloadFile(content: string | Blob, filename: string, mimeType: string) {
  const blob = typeof content === 'string' ? new Blob([content], { type: mimeType }) : content;
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

/**
 * 格式化当前时间为文件名友好字符串
 */
function getTimestampStr(): string {
  const d = new Date();
  return `${d.getFullYear()}${String(d.getMonth() + 1).padStart(2, '0')}${String(d.getDate()).padStart(2, '0')}_${String(d.getHours()).padStart(2, '0')}${String(d.getMinutes()).padStart(2, '0')}`;
}

/**
 * 导出为 Markdown (.md)
 */
export function exportChatToMarkdown(messages: ExportMessage[], sessionTitle: string, labels: ChatExportLabels) {
  const safeTitle = sessionTitle.replace(/[\\/:*?"<>|]/g, '_').trim() || labels.heading;
  const now = new Date().toLocaleString(labels.locale);

  let md = `# ${labels.heading}\n\n`;
  md += `> **${labels.topic}**: ${sessionTitle}  \n`;
  md += `> **${labels.exportedAt}**: ${now}  \n`;
  md += `> **${labels.messageCount}**: ${messages.length}\n\n`;
  md += `---\n\n`;

  messages.forEach((msg, idx) => {
    if (msg.role === 'user') {
      md += `### 👤 ${labels.you} (${idx + 1})\n\n`;
      md += `${msg.content}\n\n`;
    } else if (msg.role === 'assistant') {
      md += `### 🧠 ${labels.assistant} (${idx + 1})\n\n`;
      if (msg.latency_ms) {
        md += `*${labels.latency}: ${(msg.latency_ms / 1000).toFixed(2)}s*\n\n`;
      }
      md += `${msg.content}\n\n`;

      if (msg.sources && msg.sources.length > 0) {
        md += `#### 📎 ${labels.sources}:\n`;
        msg.sources.forEach((s, sIdx) => {
          const score = s.score ? ` (${labels.match}: ${(s.score * 100).toFixed(0)}%)` : '';
          md += `- [${sIdx + 1}] [${s.title}](${safePlainExportUrl(s.url)})${score}\n`;
        });
        md += `\n`;
      }
    } else if (msg.role === 'system') {
      md += `> ⚠️ **${labels.system}**: ${msg.content}\n\n`;
    }
    md += `---\n\n`;
  });

  downloadFile(md, `${safeTitle}_${getTimestampStr()}.md`, 'text/markdown;charset=utf-8');
}

/**
 * 导出为高保真 Word 文档 (.doc)
 */
export function buildChatWordDocument(messages: ExportMessage[], sessionTitle: string, labels: ChatExportLabels): string {
  const now = new Date().toLocaleString(labels.locale);

  let htmlBody = `
  <div style="font-family: 'PingFang SC', 'Microsoft YaHei', sans-serif; max-width: 800px; margin: 0 auto; color: #2C2416; line-height: 1.7;">
    <div style="border-bottom: 2px solid #E8594A; padding-bottom: 12px; margin-bottom: 24px;">
      <h1 style="color: #E8594A; font-size: 24px; margin: 0 0 8px 0;">${labels.heading}</h1>
      <p style="color: #8B7E6A; font-size: 13px; margin: 0;">${labels.topic}: <b>${escapeHtml(sessionTitle)}</b> &nbsp;|&nbsp; ${labels.exportedAt}: ${now}</p>
    </div>
  `;

  messages.forEach((msg, idx) => {
    if (msg.role === 'user') {
      htmlBody += `
      <div style="margin-bottom: 18px; text-align: right;">
        <div style="display: inline-block; text-align: left; background-color: #FEECEB; border: 1px solid #F8C3BE; border-radius: 12px; padding: 10px 16px; max-width: 85%;">
          <div style="font-size: 11px; font-weight: bold; color: #D04A3C; margin-bottom: 4px;">👤 ${labels.you} (${idx + 1})</div>
          <div style="font-size: 14px; color: #2C2416; white-space: pre-wrap;">${escapeHtml(msg.content)}</div>
        </div>
      </div>
      `;
    } else if (msg.role === 'assistant') {
      let sourcesHtml = '';
      if (msg.sources && msg.sources.length > 0) {
        sourcesHtml += `<div style="margin-top: 14px; padding-top: 10px; border-top: 1px dashed #E8DDD0; font-size: 12px; color: #5A4F3F;"><b>📎 ${labels.sources}:</b><ul style="margin: 6px 0; padding-left: 20px;">`;
        msg.sources.forEach((s, sIdx) => {
          const score = s.score ? ` (${(s.score * 100).toFixed(0)}%)` : '';
          sourcesHtml += `<li>[${sIdx + 1}] <a href="${safeExportUrl(s.url)}" style="color: #E8594A; text-decoration: underline;">${escapeHtml(s.title)}</a>${score}</li>`;
        });
        sourcesHtml += `</ul></div>`;
      }

      htmlBody += `
      <div style="margin-bottom: 24px;">
        <div style="background-color: #FFFFFF; border: 1px solid #E8DDD0; border-radius: 14px; padding: 16px 20px; box-shadow: 0 2px 8px rgba(0,0,0,0.04);">
          <div style="display: flex; justify-content: space-between; font-size: 12px; font-weight: bold; color: #5A4F3F; border-bottom: 1px solid #F0E8DE; padding-bottom: 8px; margin-bottom: 12px;">
            <span>🧠 ${labels.assistant} (${idx + 1})</span>
            ${msg.latency_ms ? `<span style="font-weight: normal; color: #8B7E6A;">${labels.latency}: ${(msg.latency_ms / 1000).toFixed(2)}s</span>` : ''}
          </div>
          <div style="font-size: 14px; color: #2C2416; white-space: pre-wrap; line-height: 1.8;">${escapeHtml(msg.content)}</div>
          ${sourcesHtml}
        </div>
      </div>
      `;
    }
    else if (msg.role === 'system') {
      htmlBody += `<div style="margin: 18px 0; white-space: pre-wrap;"><b>${labels.system}</b>: ${escapeHtml(msg.content)}</div>`;
    }
  });

  htmlBody += `
    <div style="text-align: center; margin-top: 36px; padding-top: 16px; border-top: 1px solid #E8DDD0; font-size: 11px; color: #8B7E6A;">
      ${labels.footer}
    </div>
  </div>
  `;

  return `
    <html xmlns:o='urn:schemas-microsoft-com:office:office' xmlns:w='urn:schemas-microsoft-com:office:word' xmlns='http://www.w3.org/TR/REC-html40'>
    <head>
      <meta charset='utf-8'>
      <title>${escapeHtml(sessionTitle)}</title>
    </head>
    <body>
      ${htmlBody}
    </body>
    </html>
  `;

}

export function exportChatToWord(messages: ExportMessage[], sessionTitle: string, labels: ChatExportLabels) {
  const safeTitle = sessionTitle.replace(/[\\/:*?"<>|]/g, '_').trim() || labels.heading;
  downloadFile(buildChatWordDocument(messages, sessionTitle, labels), `${safeTitle}_${getTimestampStr()}.doc`, 'application/msword;charset=utf-8');
}

/**
 * 导出为纯文本 (.txt)
 */
export function exportChatToText(messages: ExportMessage[], sessionTitle: string, labels: ChatExportLabels) {
  const safeTitle = sessionTitle.replace(/[\\/:*?"<>|]/g, '_').trim() || labels.heading;
  const now = new Date().toLocaleString(labels.locale);

  let text = `${labels.heading}\n${labels.topic}: ${sessionTitle}\n${labels.exportedAt}: ${now}\n${'='.repeat(40)}\n\n`;

  messages.forEach((msg, idx) => {
    const roleName = msg.role === 'user' ? labels.you : msg.role === 'assistant' ? labels.assistant : labels.system;
    text += `[${idx + 1}] ${roleName}:\n${msg.content}\n\n`;
    if (msg.sources && msg.sources.length > 0) {
      text += `${labels.sources}:\n`;
      msg.sources.forEach((s, sIdx) => {
        text += `  [${sIdx + 1}] ${s.title} (${safePlainExportUrl(s.url)})\n`;
      });
      text += `\n`;
    }
    text += `${'-'.repeat(30)}\n\n`;
  });

  downloadFile(text, `${safeTitle}_${getTimestampStr()}.txt`, 'text/plain;charset=utf-8');
}

/**
 * HTML 转义
 */
function escapeHtml(str: string): string {
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

function safeExportUrl(value: string): string {
  try {
    const url = new URL(value);
    return url.protocol === 'https:' || url.protocol === 'http:' ? escapeHtml(url.href) : '#';
  } catch {
    return '#';
  }
}

function safePlainExportUrl(value: string): string {
  try {
    const url = new URL(value);
    return url.protocol === 'https:' || url.protocol === 'http:' ? url.href : '#';
  } catch {
    return '#';
  }
}
