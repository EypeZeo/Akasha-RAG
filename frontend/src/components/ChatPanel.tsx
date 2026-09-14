import { useState, useRef, useEffect, useLayoutEffect, useCallback, FormEvent } from 'react';
import { flushSync } from 'react-dom';
import { useVirtualizer } from '@tanstack/react-virtual';
import * as api from '../api';
import ChatSessionDrawer from './ChatSessionDrawer';
import { exportChatToMarkdown, exportChatToWord, exportChatToText, type ChatExportLabels } from '../utils/chatExport';
import { useI18n } from '../i18n';
import { useWorkspaceStore, type Platform } from '../store/workspace';
import { isNearBottom } from '../utils/chatScroll';
import ApiKeyMissingModal from './ApiKeyMissingModal';
import ChatMessageRow, { type Message, type TraceData } from './ChatMessageRow';

interface Props {
  collectionId: string;
  platform?: string;
  statsRefreshKey: number;
  activeSessionId?: number | null;
  onSelectSession?: (id: number | null) => void;
  /** 面板是否处于可见的 Tab（隐藏时虚拟器不应绑定 0 高度的滚动容器） */
  active?: boolean;
  onOpenSettings: () => void;
}

const MSG_PAGE = 30;
const QUICK_EMOJIS = ['💡', '📝', '🔍', '📊', '❓', '🎯', '🚀', '🧠', '📌', '✨'];

const newKey = (): string => {
  try {
    return crypto.randomUUID();
  } catch {
    return `k-${Date.now()}-${Math.random().toString(36).slice(2)}`;
  }
};

const dbKey = (id: number) => `db:${id}`;

export default function ChatPanel({ collectionId, platform, statsRefreshKey, activeSessionId, onSelectSession, active = true, onOpenSettings }: Props) {
  const { t, lang } = useI18n();
  const scopePlatform = useWorkspaceStore(s => s.selectedPlatform);
  const setSelectedPlatform = useWorkspaceStore(s => s.setSelectedPlatform);
  const setSelectedCollectionId = useWorkspaceStore(s => s.setSelectedCollectionId);
  const [messages, setMessages] = useState<Message[]>([]);
  const messagesRef = useRef<Message[]>(messages);
  messagesRef.current = messages;
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [showApiKeyMissing, setShowApiKeyMissing] = useState(false);
  const [kbStats, setKbStats] = useState<{ done: number; note: number } | null>(null);
  const [sessionId, setSessionId] = useState<number | null>(null);
  const [isExpanded, setIsExpanded] = useState(false);
  const [isOverflowing, setIsOverflowing] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const modalTextareaRef = useRef<HTMLTextAreaElement>(null);
  const abortControllerRef = useRef<AbortController | null>(null);
  const [showExportMenu, setShowExportMenu] = useState(false);
  const exportMenuRef = useRef<HTMLDivElement>(null);
  const [drawerRefreshKey, setDrawerRefreshKey] = useState(0);
  const [msgHasMore, setMsgHasMore] = useState(false);
  const [loadingEarlier, setLoadingEarlier] = useState(false);
  const [showEmoji, setShowEmoji] = useState(false);
  const emojiWrapRef = useRef<HTMLDivElement>(null);
  const [printAll, setPrintAll] = useState(false);
  const [animatingKeys, setAnimatingKeys] = useState<Set<string>>(() => new Set());

  // 请求代际隔离：只有当前代的流事件可以更新对应消息
  const generationRef = useRef<string | null>(null);
  // 逐 token 合并：SSE delta 先入 buffer，每帧 flush 一次
  const pendingDeltaRef = useRef('');
  const rafRef = useRef<number | null>(null);
  // 智能贴底：用户在底部附近才自动跟随流式输出
  const stickToBottomRef = useRef(true);
  // 前置分页锚点：记录 setMessages 前首个可见行，测量后精确复位
  const pendingAnchorRef = useRef<{ prependCount: number } | null>(null);

  // ChatPanel 常驻挂载，初始在 hidden 的 Tab 面板里（scroll 容器 display:none、
  // clientHeight=0）。@tanstack/react-virtual 首帧会把 scrollRect 缓存成 0×0，
  // 之后 `outerSize>0` 判定失败 → `getVirtualItems()` 永远返回 []；`.measure()`
  // 只清行高缓存、不碰 scrollRect，ResizeObserver 在 hidden→显示 边界又不稳定。
  // 解决：面板不可见时 getScrollElement 返回 null；`active` 变 true 后（此渲染
  // 提交时 hidden 已摘除、容器已有布局）返回真实元素 → react-virtual 的
  // `_willUpdate` layout-effect 立即 observeElementRect 并读到正确尺寸。
  const [scrollElReady, setScrollElReady] = useState(active);
  if (active && !scrollElReady) setScrollElReady(true);

  // 动态行高估计：用已测行的滑动平均。默认值偏大时前置分页的估算偏移会很大，
  // 让估计贴近实际能显著减小「加载更早」后的定位误差。
  const estRowSizeRef = useRef(180);

  const rowVirtualizer = useVirtualizer({
    count: messages.length,
    getScrollElement: () => (scrollElReady ? scrollRef.current : null),
    estimateSize: () => estRowSizeRef.current,
    overscan: 6,
    getItemKey: useCallback((i: number) => messages[i]?.clientKey ?? String(i), [messages]),
  });
  const virtualizerRef = useRef(rowVirtualizer);
  virtualizerRef.current = rowVirtualizer;

  // 收集已测行高 → 更新估计值；变化明显时重排
  useEffect(() => {
    const cache = (rowVirtualizer as unknown as { itemSizeCache?: Map<string, number> })
      .itemSizeCache;
    if (!cache || cache.size < 3) return;
    const sizes = [...cache.values()].filter(s => typeof s === 'number' && s > 0);
    if (sizes.length < 3) return;
    const avg = sizes.reduce((a, b) => a + b, 0) / sizes.length;
    if (Math.abs(avg - estRowSizeRef.current) > 24) {
      estRowSizeRef.current = Math.round(avg);
      rowVirtualizer.measure();
    }
  });

  // 贴到底部：用 ref 读最新消息数（避免闭包旧值）。
  // `settle` 为真时短时间内多次重试——虚拟行首帧是估高，measureElement 落定后
  // 总高会变，一次 scroll 不够（用于会话加载 / 发送）；流式期间每次调用不重试。
  const scrollBottomTimersRef = useRef<number[]>([]);
  const scrollToBottom = (settle = false) => {
    const once = () => {
      const el = scrollRef.current;
      if (!el) return;
      // 直接顶到底 + 派发 scroll 让虚拟器同步 range（scrollHeight = 虚拟总高）
      el.scrollTop = el.scrollHeight;
      el.dispatchEvent(new Event('scroll'));
    };
    scrollBottomTimersRef.current.forEach(clearTimeout);
    scrollBottomTimersRef.current = [];
    once();
    if (settle) {
      // 虚拟行估高 → measureElement 落定后总高变，多次重顶收敛
      scrollBottomTimersRef.current = [16, 48, 100, 180, 300, 500].map(d =>
        window.setTimeout(once, d),
      );
    }
  };

  // 面板从 hidden 变可见 / 虚拟器首次就绪：把已有会话贴到底部
  useEffect(() => {
    if (scrollElReady && messagesRef.current.length && !printAll) {
      scrollToBottom(true);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scrollElReady]);

  const markAnimating = (keys: string[]) => {
    setAnimatingKeys(new Set(keys));
    window.setTimeout(() => setAnimatingKeys(new Set()), 1000);
  };

  // 前置分页后的滚动定位：把「新旧消息的分界」对到视口顶部——
  // 之前正在读的消息留在折叠线下方一屏内，新加载的更早消息在其上方待滚，
  // 不会跳到不相关的位置。虚拟行估高在 measureElement 落定后会漂移，
  // 短时间内多次 scrollToIndex 收敛（不依赖 rAF）。
  useLayoutEffect(() => {
    const anchor = pendingAnchorRef.current;
    if (!anchor) return;
    pendingAnchorRef.current = null;
    const snap = () => {
      const el = scrollRef.current;
      const v = virtualizerRef.current;
      if (!el) return;
      const info = v.getOffsetForIndex?.(anchor.prependCount, 'start');
      const target = Array.isArray(info) ? info[0] : typeof info === 'number' ? info : null;
      // 直接设 scrollTop，绕开 react-virtual 自身带 rAF/重试的滚动流程；
      // 主动派发 scroll 事件让虚拟器的 observeElementOffset 立即同步
      if (typeof target === 'number' && Math.abs(el.scrollTop - target) > 1) {
        el.scrollTop = target;
        el.dispatchEvent(new Event('scroll'));
      }
    };
    snap();
    const timers = [0, 16, 32, 64, 120, 240, 400, 600].map(d => window.setTimeout(snap, d));
    return () => timers.forEach(clearTimeout);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [messages]);

  const handleNewChat = () => {
    onSelectSession?.(null);
    setSessionId(null);
    setMessages([]);
    setInput('');
    setMsgHasMore(false);
  };

  const loadEarlierMessages = async () => {
    const oldest = messages.find(m => m.id != null)?.id;
    if (!sessionId || oldest == null || loadingEarlier) return;
    const sid = sessionId;
    setLoadingEarlier(true);
    try {
      const res = await api.getSessionMessages(sid, { before: oldest, limit: MSG_PAGE });
      if (res.success && res.items && res.items.length) {
        pendingAnchorRef.current = { prependCount: res.items.length };
        setMsgHasMore(Boolean(res.has_more));
        setMessages(prev => [
          ...res.items.map((m): Message => ({
            clientKey: dbKey(m.id),
            id: m.id,
            role: m.role as 'user' | 'assistant',
            content: m.content,
            sources: m.sources,
            latency_ms: m.latency_ms,
          })),
          ...prev,
        ]);
      }
    } catch { /* ignore */ } finally {
      setLoadingEarlier(false);
    }
  };

  const handleSessionDeleted = (id: number) => {
    if (id === sessionId) handleNewChat();
  };

  // 中断当前回答流式生成（本地视图态；后端该轮不落库）
  const handleStopGeneration = () => {
    abortControllerRef.current?.abort();
    abortControllerRef.current = null;
    generationRef.current = null;
    if (rafRef.current != null) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }
    setLoading(false);
    setMessages(prev =>
      prev.map(m => (m.isStreaming ? { ...m, isStreaming: false, interrupted: true } : m)),
    );
  };

  // 知识库统计（用于空状态自适应文案）
  useEffect(() => {
    api.getKnowledgeStats()
      .then(r => {
        if (r?.success) {
          setKbStats({
            done: r.video_cache?.done ?? 0,
            note: r.detail?.note?.done ?? 0,
          });
        }
      })
      .catch(() => {});
  }, [statsRefreshKey]);

  // 点击外部关闭导出下拉菜单
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (exportMenuRef.current && !exportMenuRef.current.contains(e.target as Node)) {
        setShowExportMenu(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const getExportLabels = (): ChatExportLabels => ({
    locale: lang === 'zh' ? 'zh-CN' : lang,
    heading: t('exportFileHeading'),
    topic: t('exportTopic'),
    exportedAt: t('exportTime'),
    messageCount: t('exportMessageCount'),
    you: t('exportYou'),
    assistant: t('exportAssistant'),
    system: t('exportSystem'),
    latency: t('exportLatency'),
    sources: t('exportSources'),
    match: t('exportMatch'),
    footer: t('exportFooter'),
  });

  const handleExportMd = () => {
    const title = sessionId ? `${t('sessionPrefix')}_${sessionId}` : t('sessionFileTitle');
    exportChatToMarkdown(messages, title, getExportLabels());
    setShowExportMenu(false);
  };

  const handleExportDoc = () => {
    const title = sessionId ? `${t('sessionPrefix')}_${sessionId}` : t('sessionFileTitle');
    exportChatToWord(messages, title, getExportLabels());
    setShowExportMenu(false);
  };

  const handleExportTxt = () => {
    const title = sessionId ? `${t('sessionPrefix')}_${sessionId}` : t('sessionFileTitle');
    exportChatToText(messages, title, getExportLabels());
    setShowExportMenu(false);
  };

  const handleExportPdf = () => {
    setShowExportMenu(false);
    // 打印前全量渲染（跳过虚拟化），布局稳定后再 print
    flushSync(() => setPrintAll(true));
    requestAnimationFrame(() =>
      requestAnimationFrame(() => {
        window.print();
        setPrintAll(false);
      }),
    );
  };

  // 系统快捷键 Ctrl+P 也要全量渲染
  useEffect(() => {
    const before = () => flushSync(() => setPrintAll(true));
    const after = () => setPrintAll(false);
    window.addEventListener('beforeprint', before);
    window.addEventListener('afterprint', after);
    return () => {
      window.removeEventListener('beforeprint', before);
      window.removeEventListener('afterprint', after);
    };
  }, []);

  // 记录用户是否在底部附近（决定流式时是否自动跟随）
  const handleScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    stickToBottomRef.current = isNearBottom(el);
  };

  const insertEmoji = (e: string) => {
    const ta = textareaRef.current;
    if (ta) {
      const s = ta.selectionStart ?? input.length;
      const en = ta.selectionEnd ?? input.length;
      const next = input.slice(0, s) + e + input.slice(en);
      setInput(next);
      requestAnimationFrame(() => {
        ta.focus();
        const pos = s + e.length;
        ta.setSelectionRange(pos, pos);
      });
    } else {
      setInput(p => p + e);
    }
    setShowEmoji(false);
  };

  // emoji 弹层：点外部 / Esc 关闭
  useEffect(() => {
    if (!showEmoji) return;
    const onDown = (e: MouseEvent) => {
      if (emojiWrapRef.current && !emojiWrapRef.current.contains(e.target as Node)) setShowEmoji(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { setShowEmoji(false); textareaRef.current?.focus(); }
    };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [showEmoji]);

  useEffect(() => () => {
    if (rafRef.current != null) cancelAnimationFrame(rafRef.current);
    scrollBottomTimersRef.current.forEach(clearTimeout);
  }, []);

  // 检测输入框内容是否超出可视高度，触发边缘呼吸提示
  useEffect(() => {
    if (textareaRef.current) {
      const isOver = textareaRef.current.scrollHeight > textareaRef.current.clientHeight + 8;
      setIsOverflowing(isOver);
    }
  }, [input]);

  // 全屏模式打开时自动聚焦
  useEffect(() => {
    if (isExpanded && modalTextareaRef.current) {
      modalTextareaRef.current.focus();
    }
  }, [isExpanded]);

  // 全局快捷键 Alt+E 快速展开/收起输入大窗
  useEffect(() => {
    const handleGlobalKeyDown = (e: KeyboardEvent) => {
      if (e.altKey && (e.key === 'e' || e.key === 'E')) {
        e.preventDefault();
        setIsExpanded(prev => !prev);
      }
    };
    window.addEventListener('keydown', handleGlobalKeyDown);
    return () => window.removeEventListener('keydown', handleGlobalKeyDown);
  }, []);

  const sessionIdRef = useRef<number | null>(sessionId);
  sessionIdRef.current = sessionId;

  // 监听选中的历史会话 ID 变化
  useEffect(() => {
    if (activeSessionId) {
      if (activeSessionId === sessionIdRef.current && messages.length > 0) return;
      setLoading(true);
      api.getSessionMessages(activeSessionId, { limit: MSG_PAGE })
        .then(res => {
          if (res.success && res.items) {
            setSessionId(activeSessionId);
            setMsgHasMore(Boolean(res.has_more));
            setMessages(res.items.map((m): Message => ({
              clientKey: dbKey(m.id),
              id: m.id,
              role: m.role as 'user' | 'assistant',
              content: m.content,
              sources: m.sources,
              latency_ms: m.latency_ms,
            })));
            stickToBottomRef.current = true;
            scrollToBottom(true);
          }
        })
        .catch(err => {
          console.error('加载历史消息失败:', err);
        })
        .finally(() => {
          setLoading(false);
        });
    } else if (activeSessionId === null && sessionIdRef.current !== null) {
      // 开启新对话
      setSessionId(null);
      setMessages([]);
    }
  }, [activeSessionId]);

  const handleSendMessage = async (textToSend: string) => {
    const q = textToSend.trim();
    if (!q || loading) return;

    try {
      const status = await api.getSettingsStatus();
      if (!status.chat_ready) {
        setShowApiKeyMissing(true);
        return;
      }
    } catch {
      // Status check failing (e.g. offline) shouldn't block sending — the
      // real chat request will surface its own error.
    }

    setInput('');
    setIsExpanded(false);
    setShowEmoji(false);
    setLoading(true);

    const genId = newKey();
    generationRef.current = genId;
    stickToBottomRef.current = true;

    const userKey = newKey();
    const asstKey = newKey();
    const userMsg: Message = { clientKey: userKey, role: 'user', content: q };
    const assistantMsg: Message = {
      clientKey: asstKey, genId, role: 'assistant', content: '', isStreaming: true,
    };
    setMessages(prev => [...prev, userMsg, assistantMsg]);
    markAnimating([userKey, asstKey]);
    scrollToBottom(true);

    const controller = new AbortController();
    abortControllerRef.current = controller;

    /** update this generation's assistant message by clientKey, guarded by genId */
    const patchAsst = (updater: (m: Message) => Message) => {
      if (generationRef.current !== genId) return;
      setMessages(prev => prev.map(m => (m.clientKey === asstKey ? updater(m) : m)));
    };

    const flushDelta = () => {
      rafRef.current = null;
      const buffered = pendingDeltaRef.current;
      if (!buffered || generationRef.current !== genId) return;
      pendingDeltaRef.current = '';
      setMessages(prev =>
        prev.map(m => (m.clientKey === asstKey ? { ...m, content: m.content + buffered } : m)),
      );
      if (stickToBottomRef.current) scrollToBottom();
    };

    try {
      const stream = api.chatAskStream(q, sessionId, collectionId, platform, controller.signal);

      for await (const event of stream) {
        if (generationRef.current !== genId) break; // 已被新请求取代

        if (event._event === 'sources') {
          patchAsst(m => ({ ...m, sources: event.sources || [] }));
        } else if (event._event === 'delta') {
          pendingDeltaRef.current += event.text || '';
          if (rafRef.current == null) rafRef.current = requestAnimationFrame(flushDelta);
        } else if (event._event === 'meta') {
          if (event.session_id) {
            setSessionId(event.session_id);
            onSelectSession?.(event.session_id);
            setDrawerRefreshKey(k => k + 1);
          }
          patchAsst(m => ({
            ...m,
            latency_ms: event.latency_ms,
            trace: event.trace,
            sources: event.sources || m.sources,
          }));
        } else if (event._event === 'done') {
          if (rafRef.current != null) { cancelAnimationFrame(rafRef.current); rafRef.current = null; }
          flushDelta();
          patchAsst(m => ({ ...m, isStreaming: false }));
          setDrawerRefreshKey(k => k + 1);
        } else if (event._event === 'error') {
          patchAsst(() => ({
            clientKey: asstKey, role: 'system',
            content: t('chatGenerationFailed'), isStreaming: false,
          }));
        }
      }
    } catch (err: any) {
      if (err.name === 'AbortError') {
        // 用户主动停止：把这一轮的 assistant 标为「已停止」（本地视图态）
        setMessages(prev =>
          prev.map(m =>
            m.clientKey === asstKey ? { ...m, isStreaming: false, interrupted: true } : m,
          ),
        );
        return;
      }
      patchAsst(() => ({
        clientKey: asstKey, role: 'system',
        content: t('chatRequestFailed'), isStreaming: false,
      }));
    } finally {
      // 只有仍是当前代才做收尾，避免清掉后一个请求的状态
      if (generationRef.current === genId) {
        abortControllerRef.current = null;
        if (rafRef.current != null) { cancelAnimationFrame(rafRef.current); rafRef.current = null; }
        flushDelta();
        setMessages(prev =>
          prev.map(m => (m.clientKey === asstKey && m.isStreaming ? { ...m, isStreaming: false } : m)),
        );
        setLoading(false);
      }
    }
  };

  const handleSubmit = (e?: FormEvent) => {
    if (e) e.preventDefault();
    handleSendMessage(input);
  };

  const kbReady = (kbStats?.done ?? 0) > 0;
  const scopeHint = collectionId !== 'all' ? `（${t('searchCollectionOnly')}）` : '';
  const emptySubtitle = kbReady
    ? t('kbReadyWithCount', {
        done: kbStats!.done,
        notes: kbStats!.note ? t('kbNotesIncluded', { count: kbStats!.note }) : '',
        scope: scopeHint,
      })
    : t('welcomeDesc');
  const promptChips = [
    t('promptSummary'),
    t('promptActionSteps'),
    t('promptContradictions'),
    t('promptCategorize'),
    t('promptRepeated'),
  ];

  return (
    <div className="h-full flex bg-[var(--color-panel)]">
      <ChatSessionDrawer
        refreshKey={drawerRefreshKey}
        onSessionDeleted={handleSessionDeleted}
        onNewChat={handleNewChat}
      />
      <div className="flex-1 flex flex-col min-w-0">
      {/* Top Header of Chat */}
      <div className="relative flex items-center justify-between px-6 py-2.5 bg-[var(--color-panel)] flex-shrink-0 z-10">
        <div className="flex items-center gap-2.5">
          {/* Session Indicator Pill */}
          <div className="flex items-center gap-1.5 px-3 py-1 rounded-full bg-amber/10 border border-amber/20">
            <span className="w-1.5 h-1.5 rounded-full bg-amber-500/90 animate-pulse" />
            <span className="text-xs font-semibold text-[var(--color-ink)] tracking-wide">
              {sessionId ? `${t('sessionPrefix')} #${sessionId}` : t('newSession')}
            </span>
          </div>

          {/* Retrieval scope switcher */}
          <div className="flex items-center gap-1 p-0.5 rounded-full bg-black/[0.04] text-[11px]">
            {(['all', 'douyin', 'bilibili'] as Platform[]).map(p => (
              <button
                key={p}
                type="button"
                onClick={() => setSelectedPlatform(p)}
                className={`px-2.5 py-1 rounded-full font-medium transition-all cursor-pointer ${
                  scopePlatform === p
                    ? 'bg-[var(--color-panel)] border border-[var(--color-border)] text-accent'
                    : 'text-[var(--color-ink-muted)] hover:text-[var(--color-ink)]'
                }`}
              >
                {p === 'all' ? `🌐 ${t('scopeAll')}` : p === 'douyin' ? t('platformDouyin') : t('platformBilibili')}
              </button>
            ))}
          </div>
          {collectionId !== 'all' && (
            <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full bg-accent/10 text-accent text-[11px] font-medium border border-accent/20">
              <span>📁</span>
              <span>{t('searchCollectionOnly')}</span>
              <button
                type="button"
                onClick={() => setSelectedCollectionId('all')}
                title={t('scopeAll')}
                className="ml-0.5 hover:text-red-500 cursor-pointer"
              >
                ✕
              </button>
            </span>
          )}
        </div>

        {/* Action Controls */}
        <div className="flex items-center gap-2">
          {/* 导出对话下拉菜单 */}
          {messages.length > 0 && (
            <div className="relative" ref={exportMenuRef}>
              <button
                type="button"
                onClick={() => setShowExportMenu(prev => !prev)}
                className="group flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-medium text-accent bg-accent/8 hover:bg-accent/15 border border-accent/25 shadow-2xs hover:shadow-xs transition-all cursor-pointer"
                title={t('exportChatTooltip')}
              >
                <span>📤</span>
                <span>{t('exportChat')}</span>
                <span className={`text-[9px] transition-transform ${showExportMenu ? 'rotate-180' : ''}`}>▼</span>
              </button>

              {showExportMenu && (
                <div className="absolute right-0 top-full mt-1.5 z-50 w-52 py-1.5 bg-white/95 backdrop-blur-md rounded-2xl shadow-xl border border-black/10 flex flex-col gap-0.5 animate-in fade-in zoom-in-95 duration-150">
                  <div className="px-3 py-1 text-[10px] font-bold text-[var(--color-ink-muted)] uppercase tracking-wider border-b border-black/5">
                    {t('exportFormat')}
                  </div>

                  <button
                    type="button"
                    onClick={handleExportMd}
                    className="flex items-center gap-2.5 px-3 py-2 text-xs text-[var(--color-ink)] hover:bg-accent/10 hover:text-accent text-left transition-colors cursor-pointer"
                  >
                    <span className="text-sm">📝</span>
                    <div className="flex flex-col">
                      <span className="font-semibold">Markdown (.md)</span>
                      <span className="text-[10px] text-[var(--color-ink-muted)]">{t('exportMdDesc')}</span>
                    </div>
                  </button>

                  <button
                    type="button"
                    onClick={handleExportDoc}
                    className="flex items-center gap-2.5 px-3 py-2 text-xs text-[var(--color-ink)] hover:bg-accent/10 hover:text-accent text-left transition-colors cursor-pointer"
                  >
                    <span className="text-sm">📄</span>
                    <div className="flex flex-col">
                      <span className="font-semibold">{t('exportDocTitle')}</span>
                      <span className="text-[10px] text-[var(--color-ink-muted)]">{t('exportDocDesc')}</span>
                    </div>
                  </button>

                  <button
                    type="button"
                    onClick={handleExportPdf}
                    className="flex items-center gap-2.5 px-3 py-2 text-xs text-[var(--color-ink)] hover:bg-accent/10 hover:text-accent text-left transition-colors cursor-pointer"
                  >
                    <span className="text-sm">🖨️</span>
                    <div className="flex flex-col">
                      <span className="font-semibold">{t('exportPdfTitle')}</span>
                      <span className="text-[10px] text-[var(--color-ink-muted)]">{t('exportPdfDesc')}</span>
                    </div>
                  </button>

                  <button
                    type="button"
                    onClick={handleExportTxt}
                    className="flex items-center gap-2.5 px-3 py-2 text-xs text-[var(--color-ink)] hover:bg-accent/10 hover:text-accent text-left transition-colors cursor-pointer"
                  >
                    <span className="text-sm">📃</span>
                    <div className="flex flex-col">
                      <span className="font-semibold">{t('exportTxtTitle')}</span>
                      <span className="text-[10px] text-[var(--color-ink-muted)]">{t('exportTxtDesc')}</span>
                    </div>
                  </button>
                </div>
              )}
            </div>
          )}

          {(messages.length > 0 || sessionId) && (
            <button
              onClick={() => {
                setSessionId(null);
                setMessages([]);
                onSelectSession?.(null);
              }}
              className="group relative flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-medium text-[var(--color-ink-muted)] hover:text-accent bg-white/80 hover:bg-white border border-black/[0.06] hover:border-accent/30 shadow-2xs hover:shadow-xs transition-all cursor-pointer overflow-hidden"
              title={t('clearChatDesc')}
            >
              <span className="text-accent group-hover:rotate-90 transition-transform duration-300 font-bold">➕</span>
              <span>{t('newChat')}</span>
            </button>
          )}
        </div>

        {/* 底部柔和渐变过渡线，告别生硬的 1px 褐线 */}
        <div className="gradient-divider absolute bottom-0 left-0 right-0" />
      </div>

      {/* Messages Scroll Area */}
      <div ref={scrollRef} onScroll={handleScroll} className="flex-1 overflow-y-auto p-6 subtle-scrollbar chat-virtual-list">
        {messages.length === 0 ? (
          <div className="h-full flex flex-col items-center justify-center gap-7 text-center px-6">
            <div className="w-16 h-16 rounded-2xl flex items-center justify-center bg-gradient-to-br from-accent to-amber shadow-lg shadow-accent/15">
              <span className="text-2xl">🧠</span>
            </div>
            <div className="space-y-2">
              <h2 className="text-2xl font-semibold text-[var(--color-ink)] tracking-tight">
                {t('welcomeGreeting')}
              </h2>
              <p className="text-sm text-[var(--color-ink-muted)]">{emptySubtitle}</p>
            </div>
            {kbReady ? (
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5 w-full max-w-xl">
                {promptChips.map((text, i) => {
                  const glyph = text.match(/^\S+/)?.[0] ?? '·';
                  const label = text.replace(/^\S+\s/, '');
                  return (
                    <button
                      key={i}
                      onClick={() => handleSendMessage(label)}
                      disabled={loading}
                      className="welcome-suggest-card group disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer"
                    >
                      <span className="text-base leading-none mt-0.5">{glyph}</span>
                      <span className="text-[13px] text-[var(--color-ink-soft)] group-hover:text-[var(--color-ink)]">
                        {label}
                      </span>
                    </button>
                  );
                })}
              </div>
            ) : (
              <p className="text-xs text-[var(--color-ink-muted)] max-w-xs">{t('welcomeTip')}</p>
            )}
          </div>
        ) : (
          <div className="max-w-3xl mx-auto w-full">
            {msgHasMore && (
              <div className="flex justify-center pb-4">
                <button
                  type="button"
                  onClick={loadEarlierMessages}
                  disabled={loadingEarlier}
                  className="px-3 py-1 rounded-full text-[11px] text-[var(--color-ink-muted)] bg-black/[0.04] hover:bg-black/[0.08] disabled:opacity-50 cursor-pointer transition-colors"
                >
                  {loadingEarlier ? t('loadingVideos') : t('loadEarlierMessages')}
                </button>
              </div>
            )}

            {printAll ? (
              <div className="flex flex-col gap-6 pb-2">
                {messages.map(msg => (
                  <ChatMessageRow key={msg.clientKey} msg={msg} animating={false} />
                ))}
              </div>
            ) : (
              <div style={{ height: rowVirtualizer.getTotalSize(), position: 'relative' }}>
                {rowVirtualizer.getVirtualItems().map(vi => {
                  const msg = messages[vi.index];
                  if (!msg) return null;
                  return (
                    <div
                      key={msg.clientKey}
                      data-index={vi.index}
                      data-key={msg.clientKey}
                      ref={rowVirtualizer.measureElement}
                      style={{ position: 'absolute', top: 0, left: 0, width: '100%', transform: `translateY(${vi.start}px)` }}
                    >
                      <div className="pb-6">
                        <ChatMessageRow msg={msg} animating={animatingKeys.has(msg.clientKey)} />
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Chat Composer — 悬浮感 in-flow 胶囊 */}
      <div className="relative px-4 pt-2 pb-3 chat-input-isolate chat-composer-region">
        <div className="max-w-3xl mx-auto">
          <div ref={emojiWrapRef} className="relative">
            {showEmoji && (
              <div
                role="menu"
                aria-label={t('quickEmoji')}
                className="absolute bottom-[3.25rem] left-1 z-20 p-2 grid grid-cols-5 gap-1 rounded-2xl border border-[var(--color-border)] bg-white shadow-xl animate-in fade-in zoom-in-95 duration-150"
              >
                {QUICK_EMOJIS.map(e => (
                  <button
                    key={e}
                    type="button"
                    onClick={() => insertEmoji(e)}
                    className="w-8 h-8 rounded-lg hover:bg-black/5 text-base cursor-pointer"
                  >
                    {e}
                  </button>
                ))}
              </div>
            )}

            <form onSubmit={handleSubmit} className="chat-capsule flex items-end gap-1.5 px-2.5 py-2">
              <button
                type="button"
                onClick={() => setShowEmoji(v => !v)}
                aria-haspopup="menu"
                aria-expanded={showEmoji}
                title={t('quickEmoji')}
                className="shrink-0 w-9 h-9 rounded-full flex items-center justify-center text-base text-[var(--color-ink-muted)] hover:bg-black/5 hover:text-[var(--color-ink)] transition-colors cursor-pointer"
              >
                😊
              </button>

              <textarea
                ref={textareaRef}
                value={input}
                onChange={e => setInput(e.target.value)}
                onKeyDown={e => {
                  if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    handleSubmit(e as any);
                  }
                }}
                rows={1}
                placeholder={loading ? t('aiGeneratingPlaceholder') : t('inputPlaceholder')}
                className="flex-1 min-w-0 bg-transparent text-sm outline-none resize-none leading-relaxed py-1.5 max-h-40 subtle-scrollbar overflow-y-auto placeholder:text-[var(--color-ink-muted)]"
                disabled={loading}
              />

              <button
                type="button"
                onClick={() => setIsExpanded(true)}
                title={isOverflowing ? t('overflowTooltip') : t('expandEditorNormalTooltip')}
                className={`shrink-0 w-9 h-9 rounded-full flex items-center justify-center text-[var(--color-ink-muted)] hover:bg-black/5 hover:text-accent transition-colors cursor-pointer ${
                  isOverflowing ? 'text-accent expand-badge-pulse' : ''
                }`}
              >
                <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                  <polyline points="15 3 21 3 21 9"></polyline>
                  <polyline points="9 21 3 21 3 15"></polyline>
                  <line x1="21" y1="3" x2="14" y2="10"></line>
                  <line x1="3" y1="21" x2="10" y2="14"></line>
                </svg>
              </button>

              {loading ? (
                <button
                  type="button"
                  onClick={handleStopGeneration}
                  title={t('stopTooltip')}
                  className="shrink-0 h-9 px-3 rounded-full flex items-center gap-1.5 bg-red-500 hover:bg-red-600 text-white text-xs font-bold transition-colors cursor-pointer"
                >
                  <span className="w-2 h-2 rounded-[2px] bg-white" />
                  <span>{t('stop')}</span>
                </button>
              ) : (
                <button
                  type="submit"
                  disabled={!input.trim()}
                  title={t('sendTooltip')}
                  className="shrink-0 h-9 w-9 sm:w-auto sm:px-4 rounded-full flex items-center justify-center gap-1 bg-accent hover:bg-accent-hover text-white text-sm font-bold shadow-sm disabled:opacity-40 disabled:shadow-none transition-all cursor-pointer disabled:cursor-not-allowed"
                >
                  <span className="hidden sm:inline">{t('send')}</span>
                  <span>→</span>
                </button>
              )}
            </form>
          </div>

          {(input.length > 50 || messages.length > 0) && (
            <div className="flex items-center justify-end gap-3 px-3 pt-1.5 text-[11px] text-[var(--color-ink-muted)]">
              {input.length > 50 && (
                <span className="font-mono opacity-80">
                  {t('inputStats', { characters: input.length.toLocaleString(), lines: input.split('\n').length })}
                </span>
              )}
              {messages.length > 0 && (
                <button
                  type="button"
                  onClick={() => setMessages([])}
                  title={t('clearChat')}
                  className="hover:text-red-500 transition-colors flex items-center gap-1 cursor-pointer"
                >
                  🗑 {t('clear')}
                </button>
              )}
            </div>
          )}
        </div>
      </div>

      {/* 全屏/大面积输入编辑模态框 (Expanded Input Modal) */}
      {isExpanded && (
        <div className="fixed inset-0 z-50 bg-black/45 backdrop-blur-sm flex items-center justify-center p-4 sm:p-6 animate-in fade-in duration-200">
          <div className="w-full max-w-4xl h-[85vh] bg-[var(--color-panel)] rounded-3xl border border-[var(--color-border)] shadow-2xl flex flex-col overflow-hidden">
            {/* 模态框顶部工具栏 */}
            <div className="px-6 py-3.5 border-b border-[var(--color-border)] bg-white/70 flex items-center justify-between">
              <div className="flex items-center gap-2.5">
                <span className="text-base font-bold text-[var(--color-ink)]">📝 {t('expandedTitle')}</span>
                <span className="text-xs px-2.5 py-0.5 rounded-full bg-accent/10 text-accent font-mono font-medium">
                  {t('inputStats', { characters: input.length.toLocaleString(), lines: input.split('\n').length })}
                </span>
              </div>

              <div className="flex items-center gap-3">
                <button
                  type="button"
                  onClick={() => setInput('')}
                  disabled={!input}
                  className="text-xs text-[var(--color-ink-muted)] hover:text-red-500 disabled:opacity-30 transition-colors flex items-center gap-1 cursor-pointer"
                  title={t('clearInputTooltip')}
                >
                  <span>🗑️</span>
                  <span>{t('clearInput')}</span>
                </button>

                <div className="h-4 w-px bg-[var(--color-border)]" />

                <button
                  type="button"
                  onClick={() => setIsExpanded(false)}
                  className="p-1.5 rounded-xl hover:bg-black/5 text-[var(--color-ink-muted)] hover:text-[var(--color-ink)] transition-colors cursor-pointer"
                  title={t('collapseEditorTooltip')}
                >
                  <span className="text-sm font-bold">✕ {t('collapse')}</span>
                </button>
              </div>
            </div>

            {/* 模态框正文大编辑区 */}
            <div className="flex-1 p-5 flex flex-col bg-white/40 overflow-hidden">
              <textarea
                ref={modalTextareaRef}
                value={input}
                onChange={e => setInput(e.target.value)}
                onKeyDown={e => {
                  if (e.key === 'Escape') {
                    setIsExpanded(false);
                  } else if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
                    e.preventDefault();
                    handleSubmit();
                  }
                }}
                placeholder={t('expandedPlaceholder')}
                className="w-full flex-1 p-4 bg-white border border-[var(--color-border)] rounded-2xl text-[15px] leading-relaxed text-[var(--color-ink)] outline-none resize-none subtle-scrollbar focus:border-accent/60 focus:ring-4 focus:ring-accent/10 transition-all font-normal"
              />
            </div>

            {/* 模态框底部操作栏 */}
            <div className="px-6 py-3.5 border-t border-[var(--color-border)] bg-white/80 flex items-center justify-between">
              <div className="text-xs text-[var(--color-ink-muted)] flex items-center gap-2">
                <span>💡 {t('expandedTip')}</span>
              </div>

              <div className="flex items-center gap-3">
                <button
                  type="button"
                  onClick={() => setIsExpanded(false)}
                  className="px-4 py-2 rounded-xl text-xs font-semibold text-[var(--color-ink-soft)] bg-black/5 hover:bg-black/10 transition-colors cursor-pointer"
                >
                  {t('backToChat')}
                </button>

                {loading ? (
                  <button
                    type="button"
                    onClick={handleStopGeneration}
                    className="px-5 py-2 rounded-xl flex items-center gap-1.5 bg-red-500/90 hover:bg-red-600 text-white text-xs font-bold shadow-md shadow-red-500/20 hover:shadow-lg transition-all cursor-pointer animate-pulse"
                    title={t('stopTooltip')}
                  >
                    <span className="w-2 h-2 rounded-xs bg-white" />
                    <span>{t('stop')}</span>
                  </button>
                ) : (
                  <button
                    type="button"
                    onClick={() => handleSubmit()}
                    disabled={!input.trim()}
                    className="px-5 py-2 rounded-xl flex items-center gap-2 bg-gradient-to-r from-accent to-accent-hover text-white text-xs font-bold shadow-md shadow-accent/20 hover:shadow-lg disabled:opacity-40 transition-all cursor-pointer disabled:cursor-not-allowed"
                  >
                    <span>{t('sendDirectly')}</span>
                    <span>→</span>
                  </button>
                )}
              </div>
            </div>
          </div>
        </div>
      )}
      </div>

      {showApiKeyMissing && (
        <ApiKeyMissingModal
          kind="chat"
          onClose={() => setShowApiKeyMissing(false)}
          onOpenSettings={onOpenSettings}
        />
      )}
    </div>
  );
}
