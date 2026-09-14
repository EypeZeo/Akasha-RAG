import { useState, useRef, useEffect, memo } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeHighlight from 'rehype-highlight';
import 'highlight.js/styles/github-dark.css';
import * as api from '../api';
import ExecutionTrace from './ExecutionTrace';
import { useI18n } from '../i18n';
import { safeHref } from '../utils/url';

const CITATION_STYLES = [
  { bg: 'bg-blue-500/10 hover:bg-blue-500/20', text: 'text-blue-600', border: 'border-blue-500/30' },
  { bg: 'bg-emerald-500/10 hover:bg-emerald-500/20', text: 'text-emerald-600', border: 'border-emerald-500/30' },
  { bg: 'bg-amber-500/10 hover:bg-amber-500/20', text: 'text-amber-600', border: 'border-amber-500/30' },
  { bg: 'bg-purple-500/10 hover:bg-purple-500/20', text: 'text-purple-600', border: 'border-purple-500/30' },
  { bg: 'bg-rose-500/10 hover:bg-rose-500/20', text: 'text-rose-600', border: 'border-rose-500/30' },
  { bg: 'bg-cyan-500/10 hover:bg-cyan-500/20', text: 'text-cyan-600', border: 'border-cyan-500/30' },
];

interface CitationBadgeProps {
  index: number;
  title: string;
  sources: api.SourceItem[];
  allSources?: api.SourceItem[];
  /** which message this badge belongs to — scopes the "open sources" event */
  messageKey?: string;
}

function findSourcesForCitation(title: string, sources?: api.SourceItem[], fallbackIdx?: number): api.SourceItem[] {
  if (!sources || sources.length === 0) return [];

  const raw = title.trim();
  const lower = raw.toLowerCase();

  // 1. 完全或子串包含匹配单个来源
  const directMatches = sources.filter(s => {
    const st = s.title.trim().toLowerCase();
    return st === lower || st.includes(lower) || lower.includes(st);
  });
  if (directMatches.length > 0) {
    return directMatches;
  }

  // 2. 如果包含顿号、分号、逗号，支持一段话由多个来源生成时的拆分匹配
  const parts = raw.split(/[、;；,，]/).map(p => p.trim()).filter(Boolean);
  if (parts.length > 1) {
    const multiMatches: api.SourceItem[] = [];
    for (const part of parts) {
      const pLower = part.toLowerCase();
      const matched = sources.find(s => {
        const st = s.title.trim().toLowerCase();
        return st === pLower || st.includes(pLower) || pLower.includes(st);
      });
      if (matched && !multiMatches.some(m => m.platform_item_id === matched.platform_item_id)) {
        multiMatches.push(matched);
      }
    }
    if (multiMatches.length > 0) {
      return multiMatches;
    }
  }

  // 3. 关键词/字符重叠度高精打分（防大模型缩写或微调原标题）
  let bestScore = 0;
  let bestSource: api.SourceItem | null = null;
  for (const s of sources) {
    const st = s.title.toLowerCase();
    let overlap = 0;
    for (const char of lower) {
      if (st.includes(char) && !' ，。、；：！？《》()[]#'.includes(char)) {
        overlap++;
      }
    }
    if (overlap > bestScore) {
      bestScore = overlap;
      bestSource = s;
    }
  }
  if (bestSource && bestScore >= 2) {
    return [bestSource];
  }

  // 4. 按角标索引对应来源 (fallbackIdx - 1)
  if (typeof fallbackIdx === 'number' && fallbackIdx > 0 && fallbackIdx <= sources.length) {
    return [sources[fallbackIdx - 1]];
  }

  // 5. 最终安全兜底：取模，绝不丢失知识库来源与链接
  const safeIdx = typeof fallbackIdx === 'number' && fallbackIdx > 0
    ? (fallbackIdx - 1) % sources.length
    : 0;
  return [sources[safeIdx]];
}

function CitationBadge({ index, title, sources, allSources = [], messageKey }: CitationBadgeProps) {
  const { t, lang } = useI18n();
  const [showTooltip, setShowTooltip] = useState(false);
  const openTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const closeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const handleMouseEnter = () => {
    // 清除可能存在的关闭计时器，保证鼠标移入浮窗或在角标/浮窗间切换时不闪退
    if (closeTimerRef.current) {
      clearTimeout(closeTimerRef.current);
      closeTimerRef.current = null;
    }
    // 300ms 展开延时（防视线掠过误触）
    if (!showTooltip && !openTimerRef.current) {
      openTimerRef.current = setTimeout(() => {
        setShowTooltip(true);
        openTimerRef.current = null;
      }, 300);
    }
  };

  const handleMouseLeave = () => {
    if (openTimerRef.current) {
      clearTimeout(openTimerRef.current);
      openTimerRef.current = null;
    }
    // 关键体验提升：设置 350ms 停留缓冲时间（Grace Period），消除死区判定，容许鼠标平滑移入浮窗
    if (closeTimerRef.current) clearTimeout(closeTimerRef.current);
    closeTimerRef.current = setTimeout(() => {
      setShowTooltip(false);
      closeTimerRef.current = null;
    }, 350);
  };

  const style = CITATION_STYLES[(index - 1) % CITATION_STYLES.length];
  const maxScore = sources.reduce((max, s) => Math.max(max, s.score || 0), 0);
  const scorePercent = maxScore > 0 ? Math.round(maxScore * 100) : null;

  const handleBadgeClick = (e: React.MouseEvent) => {
    e.preventDefault();
    // 点击角标通过自定义事件通知本条消息下方的来源折叠面板展开并平滑定位高亮
    window.dispatchEvent(
      new CustomEvent('open-sources-accordion', { detail: { index, messageKey } }),
    );
  };

  return (
    <span
      className="relative inline-block select-none mx-0.5"
      onMouseEnter={handleMouseEnter}
      onMouseLeave={handleMouseLeave}
    >
      {/* 论文角标按钮（移除原生 title 属性，彻底消除浏览器原生黄色提示框遮挡冲突） */}
      <button
        type="button"
        onClick={handleBadgeClick}
        className={`inline-flex items-center justify-center font-mono font-bold text-[11px] px-1.5 py-0.2 rounded-md border ${style.bg} ${style.text} ${style.border} shadow-2xs hover:scale-110 active:scale-95 transition-all duration-150 cursor-pointer no-underline align-super`}
      >
        [{index}]
      </button>

      {/* 悬浮窗与不可见交互连桥 (Hover Bridge) */}
      {showTooltip && (
        <div
          className="absolute bottom-full left-1/2 -translate-x-1/2 mb-1 z-50 w-72 min-w-[280px] max-w-[calc(100vw-2rem)] pt-1 animate-in fade-in zoom-in-95 duration-150 whitespace-normal select-text"
          onMouseEnter={handleMouseEnter}
          onMouseLeave={handleMouseLeave}
        >
          {/* 不可见的悬浮桥接层，消除 8px 空隙引起的判定丢失 */}
          <div className="absolute -bottom-3 left-0 right-0 h-4 bg-transparent pointer-events-auto" />

          <div className="p-3 bg-white/95 backdrop-blur-md rounded-2xl shadow-xl border border-black/10 text-left relative break-words">
            {/* Tooltip 气泡小箭头 */}
            <div className="absolute top-full left-1/2 -translate-x-1/2 -mt-[1px] border-4 border-transparent border-t-white/95" />

            <div className="flex items-center justify-between pb-1.5 mb-1.5 border-b border-black/5">
              <div className="flex items-center gap-1.5">
                <span className={`w-5 h-5 rounded-md flex items-center justify-center text-[10px] font-bold ${style.bg} ${style.text} border ${style.border}`}>
                  [{index}]
                </span>
                <span className="text-xs font-bold text-[var(--color-ink)]">
                  {sources.length > 1 ? t('citationMulti', { count: sources.length }) : t('citationSource')}
                </span>
              </div>
              {scorePercent !== null && (
                <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-accent/10 text-accent font-mono font-bold">
                  {t('relevanceScore', { score: scorePercent })}
                </span>
              )}
            </div>

            {/* 如果大模型引用的概括标题与来源标题不一致，显示概括要点 */}
            {title && (!sources.length || sources.every(s => s.title !== title)) && (
              <p className="text-xs font-medium text-[var(--color-ink)] line-clamp-2 leading-snug mb-2 break-words" title={title}>
                {title}
              </p>
            )}

            {/* 来源列表：多来源并列展示，左侧在抖音打开原视频，右侧显示对应视频/图文真实标题 */}
            <div className="flex flex-col divide-y divide-black/5 pt-0.5">
              {sources.length > 0 ? (
                sources.map((s, sIdx) => {
                  const sAllIdx = allSources.findIndex(x => x.platform_item_id === s.platform_item_id);
                  const sourceIndex = sAllIdx >= 0 ? sAllIdx + 1 : index;
                  const isBili = s.platform === 'bilibili' || s.platform_item_id?.startsWith('BV') || s.url?.includes('bilibili.com');
                  const realUrl = safeHref(s.url || (isBili ? `https://www.bilibili.com/video/${s.platform_item_id}` : `https://www.douyin.com/video/${s.platform_item_id}`));
                  const openLabel = isBili ? t('openInBilibili') : t('openInDouyin');

                  return (
                    <div
                      key={s.platform_item_id || sIdx}
                      className="flex items-center justify-between text-[11px] py-1.5 first:pt-0.5 last:pb-0 gap-2.5"
                    >
                      {/* 左侧：直接指向录入知识库的原视频/图文链接 */}
                      <a
                        href={realUrl}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-accent hover:text-accent-hover font-semibold flex items-center gap-1 group shrink-0"
                        title={s.title}
                      >
                        <span>▶ {openLabel}</span>
                        <span className="group-hover:translate-x-0.5 transition-transform text-[10px]">↗</span>
                      </a>

                      {/* 右侧：改为对应视频或图文的标题，点击可直接在下方来源卡片高亮定位 */}
                      <button
                        type="button"
                        onClick={(e) => {
                          e.preventDefault();
                          window.dispatchEvent(new CustomEvent('open-sources-accordion', { detail: { index: sourceIndex, messageKey } }));
                        }}
                        className="text-[10px] text-[var(--color-ink-muted)] hover:text-accent truncate max-w-[150px] text-right font-medium cursor-pointer transition-colors"
                        title={`[${sourceIndex}] ${s.title}`}
                      >
                        {s.title}
                      </button>
                    </div>
                  );
                })
              ) : (
                <div className="flex items-center justify-between text-[11px] pt-1 gap-2">
                  <a
                    href={title ? (title.startsWith('BV') ? `https://www.bilibili.com/video/${title}` : `https://www.douyin.com/video/${title}`) : '#'}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-accent hover:text-accent-hover font-semibold flex items-center gap-1 group shrink-0"
                  >
                    <span>▶ {title?.startsWith('BV') ? t('openInBilibili') : t('openInDouyin')}</span>
                    <span className="group-hover:translate-x-0.5 transition-transform text-[10px]">↗</span>
                  </a>
                  <span className="text-[10px] text-[var(--color-ink-muted)] truncate max-w-[150px] text-right font-medium" title={title}>
                    {title || t('noCitationInfo')}
                  </span>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </span>
  );
}

function CopyButton({ text }: { text: string }) {
  const { t } = useI18n();
  const [copied, setCopied] = useState(false);
  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      const ta = document.createElement('textarea');
      ta.value = text;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      document.body.removeChild(ta);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  return (
    <button
      type="button"
      onClick={handleCopy}
      className={`px-2 py-0.5 rounded-lg text-[11px] font-medium transition-all cursor-pointer flex items-center gap-1 shadow-2xs ${
        copied
          ? 'bg-emerald-50 text-emerald-600 border border-emerald-300'
          : 'bg-black/[0.03] hover:bg-black/[0.06] text-[var(--color-ink-muted)] hover:text-accent border border-black/[0.05]'
      }`}
      title={t('copy')}
    >
      <span>{copied ? '✓' : '📋'}</span>
      <span>{copied ? t('copied') : t('copy')}</span>
    </button>
  );
}

/** A fenced code block with syntax highlighting and its own copy button. */
function CodeBlock({ children }: { children: React.ReactNode }) {
  const { t } = useI18n();
  const preRef = useRef<HTMLPreElement>(null);
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    const text = preRef.current?.innerText ?? '';
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      const ta = document.createElement('textarea');
      ta.value = text;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      document.body.removeChild(ta);
    }
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="group relative my-3">
      <button
        type="button"
        onClick={handleCopy}
        title={t('copy')}
        className={`absolute right-2 top-2 z-10 px-2 py-0.5 rounded-md text-[10px] font-medium transition-all cursor-pointer opacity-0 group-hover:opacity-100 ${
          copied
            ? 'bg-emerald-500/15 text-emerald-600 border border-emerald-400/40'
            : 'bg-black/[0.06] hover:bg-black/[0.12] text-[var(--color-ink-muted)] border border-black/10'
        }`}
      >
        {copied ? `✓ ${t('copied')}` : `📋 ${t('copy')}`}
      </button>
      <pre ref={preRef} className="overflow-x-auto rounded-xl">{children}</pre>
    </div>
  );
}

function CollapsibleSources({ sources, messageKey }: { sources: api.SourceItem[]; messageKey?: string }) {
  const { t } = useI18n();
  const [isExpanded, setIsExpanded] = useState(false);

  // 监听角标点击事件：只响应本条消息发出的事件，展开来源栏并平滑滚动到指定文献
  useEffect(() => {
    const handleOpenSources = (e: any) => {
      if (e?.detail?.messageKey !== messageKey) return;
      const targetIndex = e?.detail?.index;
      setIsExpanded(true);
      setTimeout(() => {
        const el = document.getElementById(`source-card-${messageKey ?? 'x'}-${targetIndex}`);
        if (el) {
          el.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
          el.classList.add('ring-2', 'ring-accent', 'scale-102');
          setTimeout(() => el.classList.remove('ring-2', 'ring-accent', 'scale-102'), 1800);
        }
      }, 120);
    };
    window.addEventListener('open-sources-accordion', handleOpenSources as any);
    return () => window.removeEventListener('open-sources-accordion', handleOpenSources as any);
  }, [messageKey]);

  if (!sources || sources.length === 0) return null;

  return (
    <div className="mt-4 pt-3 relative">
      <div className="gradient-divider absolute top-0 left-0 right-0" />
      {/* 优雅折叠/展开栏 */}
      <button
        type="button"
        onClick={() => setIsExpanded(prev => !prev)}
        className="w-full flex items-center justify-between py-2 px-3 rounded-xl bg-black/[0.02] hover:bg-black/[0.04] border border-black/[0.05] transition-all cursor-pointer group"
      >
        <div className="flex items-center gap-2">
          <span className="text-sm">📑</span>
          <span className="text-xs font-bold text-[var(--color-ink)]">
            {t('infoAndSources')}
          </span>
          <span className="px-2 py-0.5 rounded-full bg-accent/10 text-accent font-mono font-bold text-[10px]">
            {t('citationCount', { count: sources.length })}
          </span>
        </div>

        <div className="flex items-center gap-1.5 text-[11px] text-[var(--color-ink-muted)] group-hover:text-accent transition-colors">
          <span>{isExpanded ? t('collapseSources') : t('expandSources')}</span>
          <span className={`text-[9px] transition-transform duration-200 ${isExpanded ? 'rotate-180' : ''}`}>
            ▼
          </span>
        </div>
      </button>

      {/* 展开内容列表 */}
      {isExpanded && (
        <div className="mt-2.5 grid grid-cols-1 sm:grid-cols-2 gap-2 animate-in fade-in duration-200">
          {sources.map((s, j) => {
            const style = CITATION_STYLES[j % CITATION_STYLES.length];
            return (
              <a
                id={`source-card-${messageKey ?? 'x'}-${j + 1}`}
                key={j}
                href={safeHref(s.url)}
                target="_blank"
                rel="noopener noreferrer"
                className="group flex items-center justify-between p-2.5 rounded-xl bg-white hover:bg-black/[0.01] border border-black/[0.06] hover:border-accent/40 shadow-2xs hover:shadow-xs transition-all duration-300"
                title={s.title}
              >
                <div className="flex items-center gap-2 min-w-0 flex-1">
                  <span className={`w-5 h-5 rounded-md flex items-center justify-center text-[10px] font-bold flex-shrink-0 ${style.bg} ${style.text} border ${style.border}`}>
                    [{j + 1}]
                  </span>
                  {(s.platform === 'bilibili' || s.url?.includes('bilibili.com')) ? (
                    <span className="text-[9px] px-1 py-0.2 rounded font-semibold bg-pink-50 text-pink-600 border border-pink-200/60 flex-shrink-0">
                      {t('platformBilibili')}
                    </span>
                  ) : (
                    <span className="text-[9px] px-1 py-0.2 rounded font-semibold bg-black/5 text-[var(--color-ink-soft)] border border-black/10 flex-shrink-0">
                      {t('platformDouyin')}
                    </span>
                  )}
                  <span className="text-xs text-[var(--color-ink-soft)] group-hover:text-accent font-medium truncate transition-colors">
                    {s.title}
                  </span>
                </div>
                {s.score && (
                  <span className="ml-2 px-1.5 py-0.5 rounded-md bg-accent/8 text-accent text-[10px] font-mono font-bold flex-shrink-0">
                    {(s.score * 100).toFixed(0)}%
                  </span>
                )}
              </a>
            );
          })}
        </div>
      )}
    </div>
  );
}

function preprocessCitations(content: string, sources?: api.SourceItem[]): string {
  // 移除尾部可能自动追加的 📎 **参考来源：** 块（因为已在卡片下方折叠展示）
  const cleaned = content.replace(/\n*---\s*\n+📎\s*\*\*参考来源[:：]\*\*\s*\n([\s\S]*)$/g, '').trim();

  // 映射标题与来源索引
  const titleToIndexMap = new Map<string, number>();
  let nextIdx = 1;

  if (sources && sources.length > 0) {
    sources.forEach((s, idx) => {
      titleToIndexMap.set(s.title.trim().toLowerCase(), idx + 1);
    });
    nextIdx = sources.length + 1;
  }

  // 匹配 [来源: xxx] 或 [来源：xxx]
  return cleaned.replace(/\[来源[:：]\s*([^\]]+)\]/g, (_, title) => {
    const trimmedTitle = title.trim();
    const key = trimmedTitle.toLowerCase();
    let idx = titleToIndexMap.get(key);
    if (!idx && sources) {
      const found = sources.findIndex(s => s.title.toLowerCase().includes(key) || key.includes(s.title.toLowerCase()));
      if (found >= 0) {
        idx = found + 1;
        titleToIndexMap.set(key, idx);
      }
    }
    if (!idx) {
      idx = nextIdx++;
      titleToIndexMap.set(key, idx);
    }
    return `[cite-ref:${idx}](${encodeURIComponent(trimmedTitle)})`;
  });
}

export interface TraceData {
  route: string;
  steps: { name: string; time_ms: number }[];
  chunks: { chunk_id: string; title: string; text: string; score: number }[];
}

export interface Message {
  /** Stable client-side identity — React key, virtualizer key, source-card anchors all use this. */
  clientKey: string;
  id?: number;
  /** Which generation produced this assistant message (in-memory only). */
  genId?: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  sources?: api.SourceItem[];
  isStreaming?: boolean;
  /** User hit Stop mid-stream — session-local view state, not persisted. */
  interrupted?: boolean;
  trace?: TraceData;
  latency_ms?: number;
}

/**
 * One transcript row. Module-level + memo so that a streaming token (which
 * replaces only the streaming message's object) never re-renders the other
 * rows — the virtualizer + this memo keep long threads cheap.
 */
const ChatMessageRow = memo(function ChatMessageRow({
  msg,
  animating,
}: {
  msg: Message;
  animating: boolean;
}) {
  const { t } = useI18n();
  const enter = animating ? 'msg-enter' : '';

  if (msg.role === 'user') {
    return (
      <div className={`flex w-full justify-end ${enter}`}>
        <div className="modern-user-bubble max-w-[82%] px-4 py-3 text-[0.935rem] leading-relaxed select-text">
          <div className="whitespace-pre-wrap">{msg.content}</div>
        </div>
      </div>
    );
  }

  if (msg.role === 'system') {
    return (
      <div className={`flex w-full justify-start ${enter}`}>
        <div className="max-w-[88%] px-4 py-2.5 rounded-2xl bg-red-50/95 border border-red-200/80 text-red-600 text-xs flex items-center gap-2">
          <span>⚠️</span>
          <span>{msg.content}</span>
        </div>
      </div>
    );
  }

  return (
    <div className={`flex w-full justify-start ${enter}`}>
      <div
        className={`w-full select-text ${
          msg.isStreaming
            ? 'modern-streaming-card px-3.5 py-3'
            : msg.interrupted
            ? 'modern-ai-card opacity-90'
            : 'modern-ai-card msg-settle'
        }`}
      >
        {/* quiet header */}
        <div className="flex items-center gap-2 mb-2.5 text-[var(--color-ink-muted)]">
          <span className="w-5 h-5 rounded-lg bg-accent/10 flex items-center justify-center text-[11px]">🧠</span>
          <span className="text-[11px] font-medium tracking-wide">
            {msg.isStreaming ? t('aiThinking') : t('aiOrganized')}
          </span>
          <div className="ml-auto flex items-center gap-1.5">
            {!msg.isStreaming && msg.content && <CopyButton text={msg.content} />}
            {msg.isStreaming ? (
              <span className="thinking-dots anim-fade" aria-hidden>
                <span /><span /><span />
              </span>
            ) : msg.interrupted ? (
              <span className="inline-flex items-center gap-1 text-[10px] font-medium text-amber-700 bg-amber-100/60 px-2 py-0.5 rounded-full anim-fade">
                ⏹ {t('answerStopped')}
              </span>
            ) : msg.latency_ms ? (
              <span className="font-mono text-[10px] opacity-70 anim-pop">
                {(msg.latency_ms / 1000).toFixed(2)}s
              </span>
            ) : null}
          </div>
        </div>

        {/* body */}
        {msg.isStreaming ? (
          msg.content ? (
            <div className="streaming-frame-text whitespace-pre-wrap">
              {msg.content}
              <span className="stream-caret" />
            </div>
          ) : (
            <div className="flex items-center gap-2 text-xs text-[var(--color-ink-muted)] py-1.5">
              <span className="thinking-dots" aria-hidden>
                <span /><span /><span />
              </span>
              <span className="ml-0.5">{t('retrievingVectors')}</span>
            </div>
          )
        ) : (
          <div
            className={`markdown-body text-[0.935rem] leading-[1.75] text-[var(--color-ink)] ${
              msg.interrupted ? 'interrupted-truncate' : ''
            }`}
          >
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              rehypePlugins={[[rehypeHighlight, { detect: true, ignoreMissing: true }]]}
              components={{
                pre: ({ children }) => <CodeBlock>{children}</CodeBlock>,
                a: ({ href, children, ...props }) => {
                  const childText = String(children);
                  const citeMatch = childText.match(/^cite-ref:(\d+)$/);
                  if (citeMatch) {
                    const idx = parseInt(citeMatch[1], 10);
                    const title = decodeURIComponent(href || '');
                    return (
                      <CitationBadge
                        index={idx}
                        title={title}
                        sources={findSourcesForCitation(title, msg.sources, idx)}
                        allSources={msg.sources || []}
                        messageKey={msg.clientKey}
                      />
                    );
                  }
                  return (
                    <a href={href} target="_blank" rel="noopener noreferrer" {...props}>
                      {children}
                    </a>
                  );
                },
              }}
            >
              {preprocessCitations(msg.content, msg.sources)}
            </ReactMarkdown>
          </div>
        )}

        {msg.sources && msg.sources.length > 0 && (
          <CollapsibleSources sources={msg.sources} messageKey={msg.clientKey} />
        )}
        {msg.trace && (
          <div className="mt-3.5 pt-1">
            <ExecutionTrace trace={msg.trace} latency_ms={msg.latency_ms} />
          </div>
        )}
      </div>
    </div>
  );
});

export default ChatMessageRow;
export { CitationBadge, CollapsibleSources, CopyButton, CodeBlock, findSourcesForCitation, preprocessCitations };
