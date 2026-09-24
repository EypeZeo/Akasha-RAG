import * as api from '../api';
import type { Message } from '../components/ChatMessageRow';

export const HISTORY_PAGE_SIZE = 200;
export const EXPORT_LIMITS = { messages: 10000, bytes: 8 * 1024 * 1024 };
export const PRINT_LIMITS = { messages: 2000, bytes: 2 * 1024 * 1024 };
export class ExportCapacityError extends Error {}

export function messageFromItem(item: api.MessageItem): Message {
  return { clientKey: item.client_key || `db:${item.id}`, id: item.id, role: item.role,
    content: item.content, sources: item.sources, latency_ms: item.latency_ms };
}

export function freezeMessages(messages: Message[]): Message[] {
  return messages.map(message => ({ ...message, sources: message.sources?.map(source => ({ ...source })) }));
}

export function checkExportSize(messages: Message[], limits = EXPORT_LIMITS): void {
  if (messages.length > limits.messages) throw new ExportCapacityError('Too many messages');
  let bytes = 0;
  const encoder = new TextEncoder();
  for (const message of messages) {
    bytes += encoder.encode(message.content).byteLength;
    bytes += encoder.encode(JSON.stringify(message.sources ?? [])).byteLength;
    if (bytes > limits.bytes) throw new ExportCapacityError('Transcript too large');
  }
}

function messageBytes(message: Message): number {
  const encoder = new TextEncoder();
  return encoder.encode(message.content).byteLength + encoder.encode(JSON.stringify(message.sources ?? [])).byteLength;
}

/** Persisted history at the server boundary plus the local view frozen at the click. */
export async function fetchExportHistory(
  sessionId: number,
  loaded: Message[],
  signal: AbortSignal,
  onProgress: (count: number) => void,
): Promise<Message[]> {
  signal.throwIfAborted();
  checkExportSize(loaded);
  const snapshot = await api.getSessionSnapshot(sessionId, signal);
  signal.throwIfAborted();
  if (!snapshot.success || snapshot.session_id !== sessionId ||
      !Number.isSafeInteger(snapshot.snapshot_id) || snapshot.snapshot_id < 0 ||
      !Number.isSafeInteger(snapshot.total) || snapshot.total < 0) throw new Error('Invalid history snapshot');
  if (snapshot.total > EXPORT_LIMITS.messages) throw new ExportCapacityError('Too many messages');
  const history = new Map<number, Message>();
  let historyBytes = 0;
  let before: number | undefined;
  // The extra request allows an exact multiple of the page size to end with an empty page.
  const pageLimit = Math.ceil(EXPORT_LIMITS.messages / HISTORY_PAGE_SIZE) + 1;
  for (let page = 0; snapshot.total > 0 && page < pageLimit; page++) {
    signal.throwIfAborted();
    const result = await api.getSessionMessages(sessionId, {
      limit: HISTORY_PAGE_SIZE, until: snapshot.snapshot_id, before, signal,
    });
    signal.throwIfAborted();
    if (!result.success || !Array.isArray(result.items) || result.items.length > HISTORY_PAGE_SIZE) {
      throw new Error('Invalid history page');
    }
    let previousId = 0;
    for (const item of result.items) {
      if (!Number.isSafeInteger(item.id) || item.id <= 0 || item.id > snapshot.snapshot_id ||
          item.id <= previousId || history.has(item.id) || (before !== undefined && item.id >= before) ||
          item.session_id !== sessionId || typeof item.content !== 'string') {
        throw new Error('Invalid history cursor or session');
      }
      previousId = item.id;
      const message = messageFromItem(item);
      historyBytes += messageBytes(message);
      if (historyBytes > EXPORT_LIMITS.bytes) throw new ExportCapacityError('Transcript too large');
      history.set(item.id, message);
    }
    if (history.size > snapshot.total) throw new Error('History snapshot changed');
    onProgress(history.size);
    if (history.size === snapshot.total) break;
    if (!result.items.length || !result.has_more) throw new Error('Incomplete history');
    before = Math.min(...result.items.map(item => item.id));
  }
  if (history.size !== snapshot.total) throw new Error('Incomplete history');

  const localById = new Map(loaded.filter(m => m.id != null).map(m => [m.id!, m]));
  const localByKey = new Map(loaded.map(m => [m.clientKey, m]));
  const consumed = new Set<string>();
  const merged = [...history.values()].sort((a, b) => a.id! - b.id!).map(item => {
    const local = localById.get(item.id!) ?? localByKey.get(item.clientKey);
    if (!local || consumed.has(local.clientKey)) return item;
    consumed.add(local.clientKey);
    return { ...local, id: item.id };
  });
  for (const local of loaded) {
    if (!consumed.has(local.clientKey)) {
      consumed.add(local.clientKey);
      merged.push(local);
    }
  }
  checkExportSize(merged);
  signal.throwIfAborted();
  return merged;
}
