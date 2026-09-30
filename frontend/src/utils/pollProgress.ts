/** Bound a request, including transports that ignore caller cancellation. */
export async function pollProgress<T>(
  read: (signal: AbortSignal) => Promise<T>,
  controller: AbortController,
): Promise<T> {
  let timeout: ReturnType<typeof setTimeout> | undefined;
  let onAbort: () => void = () => {};
  try {
    const cancelled = new Promise<never>((_, reject) => {
      onAbort = () => reject(new DOMException('Progress request aborted', 'AbortError'));
      if (controller.signal.aborted) onAbort();
      else controller.signal.addEventListener('abort', onAbort, { once: true });
      timeout = setTimeout(() => controller.abort(), 15_000);
    });
    return await Promise.race([read(controller.signal), cancelled]);
  } finally {
    clearTimeout(timeout);
    controller.signal.removeEventListener('abort', onAbort);
  }
}
