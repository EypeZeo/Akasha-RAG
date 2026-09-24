import { useCallback, useEffect, useRef, useState } from 'react';
import { flushSync } from 'react-dom';
import type { Message } from '../components/ChatMessageRow';
import type { PrintSnapshot } from '../components/ChatPrintDocument';
import { exportChatToMarkdown, exportChatToWord, exportChatToText, type ChatExportLabels } from '../utils/chatExport';
import { checkExportSize, ExportCapacityError, fetchExportHistory, freezeMessages, PRINT_LIMITS } from '../utils/chatHistory';

type Format = 'markdown' | 'word' | 'text' | 'pdf';
type Translate = (key: string, params?: Record<string, string | number>) => string;
interface Task {
  controller: AbortController;
  frame: number | null;
  fallback: number | null;
  printing: boolean;
}

export function useChatExport(t: Translate) {
  const [progress, setProgress] = useState<number | null>(null);
  const [printSnapshot, setPrintSnapshot] = useState<PrintSnapshot | null>(null);
  const currentRef = useRef<Task | null>(null);
  const mountedRef = useRef(true);
  const nativeSnapshotRef = useRef<(() => PrintSnapshot | null) | null>(null);
  const translateRef = useRef(t);
  translateRef.current = t;

  const invalidate = useCallback(() => {
    const task = currentRef.current;
    currentRef.current = null;
    task?.controller.abort();
    if (task?.frame != null) cancelAnimationFrame(task.frame);
    if (task?.fallback != null) window.clearTimeout(task.fallback);
    document.body.classList.remove('chat-printing');
  }, []);
  const cancel = useCallback(() => {
    invalidate();
    if (mountedRef.current) {
      setProgress(null);
      setPrintSnapshot(null);
    }
  }, [invalidate]);

  useEffect(() => {
    mountedRef.current = true;
    const before = () => {
      if (currentRef.current?.printing) return;
      const snapshot = nativeSnapshotRef.current?.();
      if (!snapshot) return;
      // Native printing owns the print document. Abort a concurrent history
      // export so its later completion cannot remove the document mid-print.
      if (currentRef.current) invalidate();
      // Native print is synchronous. Never pretend its loaded-window snapshot is full history.
      try { checkExportSize(snapshot.messages, PRINT_LIMITS); }
      catch { snapshot.messages = []; snapshot.notice = translateRef.current('exportTooLarge'); }
      document.body.classList.add('chat-printing');
      flushSync(() => {
        setProgress(null);
        setPrintSnapshot(snapshot);
      });
    };
    const after = () => cancel();
    window.addEventListener('beforeprint', before);
    window.addEventListener('afterprint', after);
    return () => {
      mountedRef.current = false;
      invalidate();
      window.removeEventListener('beforeprint', before);
      window.removeEventListener('afterprint', after);
    };
  }, [cancel, invalidate]);

  const start = (format: Format, sessionId: number | null, loaded: Message[], title: string, labels: ChatExportLabels) => {
    if (currentRef.current) return;
    const task: Task = { controller: new AbortController(), frame: null, fallback: null, printing: false };
    currentRef.current = task;
    const valid = () => mountedRef.current && currentRef.current === task && !task.controller.signal.aborted;
    const finish = () => { if (valid()) cancel(); };
    const fail = (error: unknown) => {
      if (!valid()) return;
      const tooLarge = error instanceof ExportCapacityError || (error instanceof Error && error.message.startsWith('413:'));
      cancel();
      console.error('Chat export failed:', error);
      alert(t(tooLarge ? 'exportTooLarge' : 'operationFailed'));
    };
    const build = (messages: Message[]) => {
      if (!valid()) return;
      checkExportSize(messages);
      if (format !== 'pdf') {
        ({ markdown: exportChatToMarkdown, word: exportChatToWord, text: exportChatToText })[format](messages, title, labels);
        finish();
        return;
      }
      checkExportSize(messages, PRINT_LIMITS);
      task.printing = true;
      document.body.classList.add('chat-printing');
      flushSync(() => setPrintSnapshot({ messages, title, labels }));
      task.frame = requestAnimationFrame(() => {
        if (!valid()) return;
        task.frame = requestAnimationFrame(() => {
          if (!valid()) return;
          task.frame = null;
          try {
            window.print();
            // Most engines fire afterprint. Keep a delayed fallback for engines
            // that omit it without tearing down a non-blocking print dialog.
            if (valid()) {
              task.fallback = window.setTimeout(() => {
                task.fallback = null;
                if (valid()) cancel();
              }, 60_000);
            }
          } catch (error) { fail(error); }
        });
      });
    };
    try {
      checkExportSize(loaded);
      const frozen = freezeMessages(loaded);
      setProgress(0);
      if (sessionId == null) { build(frozen); return; }
      void fetchExportHistory(sessionId, frozen, task.controller.signal, count => {
        if (valid()) setProgress(count);
      }).then(build).catch(fail);
    } catch (error) { fail(error); }
  };

  return { progress, printSnapshot, start, cancel, invalidate, nativeSnapshotRef };
}
