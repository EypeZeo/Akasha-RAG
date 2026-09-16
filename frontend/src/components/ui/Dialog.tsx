import { useEffect, useId, useLayoutEffect, useRef, useSyncExternalStore } from 'react';
import { createPortal } from 'react-dom';

/**
 * Module-level dialog stack: an ordered list of mounted Dialog instance ids,
 * most-recently-mounted last. Only the topmost one reacts to Escape,
 * backdrop click, and the Tab focus loop -- this is what lets one Dialog
 * stay mounted (and visible) underneath another without both of them
 * fighting over the same key events (e.g. SettingsModal staying open while
 * LoginModal is opened from inside it).
 */
let dialogStack: string[] = [];
const stackListeners = new Set<() => void>();

function notifyStackListeners() {
  for (const listener of stackListeners) listener();
}

function pushDialog(id: string) {
  dialogStack = [...dialogStack, id];
  notifyStackListeners();
}

function popDialog(id: string) {
  dialogStack = dialogStack.filter(existing => existing !== id);
  notifyStackListeners();
}

function subscribeToStack(listener: () => void) {
  stackListeners.add(listener);
  return () => { stackListeners.delete(listener); };
}

function getStackSnapshot() {
  return dialogStack;
}

function useIsTopmostDialog(id: string): boolean {
  const stack = useSyncExternalStore(subscribeToStack, getStackSnapshot);
  return stack.length > 0 && stack[stack.length - 1] === id;
}

const FOCUSABLE_SELECTOR =
  'button:not(:disabled), [href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])';

function getFocusableElements(container: HTMLElement): HTMLElement[] {
  // Deliberately does not filter by offsetParent/getClientRects (CSS-driven
  // visibility) -- every dialog panel in this codebase conditionally
  // *renders* its content (`{cond && <div>...}`), it never hides focusable
  // elements via CSS while leaving them in the DOM, so a layout-based check
  // has nothing real to catch here. It would also make this untestable
  // under jsdom, which never computes layout and reports offsetParent as
  // null for everything.
  return Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR));
}

export interface DialogProps {
  onClose: () => void;
  /** id of the element (usually the visible title) this dialog is labelled by. */
  labelledBy: string;
  /** Whether clicking the backdrop closes the dialog. Default true. */
  closeOnBackdropClick?: boolean;
  /** Whether pressing Escape closes the dialog. Default true. */
  closeOnEscape?: boolean;
  className?: string;
  children: React.ReactNode;
}

/**
 * Shared modal dialog shell: portals to document.body, tracks a stack of
 * simultaneously-mounted dialogs so only the topmost one responds to
 * Escape/backdrop/Tab, manages initial focus and focus restore on close,
 * and exposes aria-modal/role="dialog"/aria-labelledby. Backdrop-click and
 * Escape behavior are each opt-out per instance (closeOnBackdropClick /
 * closeOnEscape) so a caller with an in-flight submission can suppress them
 * while it's running, rather than the dialog forcing one fixed policy on
 * every caller.
 */
export default function Dialog({
  onClose,
  labelledBy,
  closeOnBackdropClick = true,
  closeOnEscape = true,
  className = '',
  children,
}: DialogProps) {
  const id = useId();
  const panelRef = useRef<HTMLDivElement>(null);
  const previouslyFocusedRef = useRef<HTMLElement | null>(null);
  const isTopmost = useIsTopmostDialog(id);

  useEffect(() => {
    pushDialog(id);
    return () => popDialog(id);
  }, [id]);

  useLayoutEffect(() => {
    previouslyFocusedRef.current = document.activeElement as HTMLElement | null;
    const panel = panelRef.current;
    const [firstFocusable] = panel ? getFocusableElements(panel) : [];
    (firstFocusable ?? panel)?.focus();
    return () => {
      previouslyFocusedRef.current?.focus?.();
    };
  }, []);

  useEffect(() => {
    if (!isTopmost) return;

    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') {
        if (closeOnEscape) onClose();
        return;
      }
      if (e.key !== 'Tab') return;
      const panel = panelRef.current;
      if (!panel) return;
      const focusables = getFocusableElements(panel);
      if (focusables.length === 0) return;
      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    }

    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [isTopmost, closeOnEscape, onClose]);

  const handleBackdropClick = (e: React.MouseEvent<HTMLDivElement>) => {
    if (!isTopmost) return;
    if (e.target !== e.currentTarget) return;
    if (closeOnBackdropClick) onClose();
  };

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/45 backdrop-blur-xs p-4"
      onClick={handleBackdropClick}
    >
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={labelledBy}
        tabIndex={-1}
        className={className}
        onClick={e => e.stopPropagation()}
      >
        {children}
      </div>
    </div>,
    document.body,
  );
}
