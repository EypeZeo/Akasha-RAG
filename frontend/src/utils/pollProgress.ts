/** Bound a request, including transports that ignore caller cancellation.
 *
 * The timeout aborts through the same controller the caller passed in, so a
 * timed-out call and a caller-cancelled call are indistinguishable at the catch
 * site.  Callers that need to tell them apart (and must not report a
 * user-initiated cancel as a backend failure) record their own cancellation
 * signal separately and pass an explicit `timeoutMs` when they need a
 * different bound.
 */
export async function pollProgress<T>(
  read: (signal: AbortSignal) => Promise<T>,
  controller: AbortController,
  timeoutMs = 15_000,
): Promise<T> {
  let timeout: ReturnType<typeof setTimeout> | undefined;
  let onAbort: () => void = () => {};
  try {
    const cancelled = new Promise<never>((_, reject) => {
      onAbort = () => reject(new DOMException('Progress request aborted', 'AbortError'));
      if (controller.signal.aborted) onAbort();
      else controller.signal.addEventListener('abort', onAbort, { once: true });
      timeout = setTimeout(() => controller.abort(), timeoutMs);
    });
    return await Promise.race([read(controller.signal), cancelled]);
  } finally {
    clearTimeout(timeout);
    controller.signal.removeEventListener('abort', onAbort);
  }
}
