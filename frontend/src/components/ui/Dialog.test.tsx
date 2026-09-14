import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach } from 'vitest';
import Dialog from './Dialog';

afterEach(cleanup);

function Panel({ id, extraButtonLabel }: { id: string; extraButtonLabel?: string }) {
  return (
    <div>
      <h2 id={id}>Dialog title</h2>
      <button type="button">first</button>
      {extraButtonLabel && <button type="button">{extraButtonLabel}</button>}
      <button type="button">last</button>
    </div>
  );
}

describe('Dialog', () => {
  it('exposes role=dialog, aria-modal and aria-labelledby pointing at a real element', () => {
    render(
      <Dialog onClose={vi.fn()} labelledBy="dlg-title">
        <Panel id="dlg-title" />
      </Dialog>,
    );
    const dialog = screen.getByRole('dialog');
    expect(dialog).toHaveAttribute('aria-modal', 'true');
    const labelId = dialog.getAttribute('aria-labelledby');
    expect(labelId).toBe('dlg-title');
    expect(document.getElementById(labelId!)).not.toBeNull();
  });

  it('clicking the backdrop closes the dialog, clicking inside the panel does not', () => {
    const onClose = vi.fn();
    render(
      <Dialog onClose={onClose} labelledBy="dlg-title">
        <Panel id="dlg-title" />
      </Dialog>,
    );
    fireEvent.click(screen.getByRole('dialog').parentElement!);
    expect(onClose).toHaveBeenCalledTimes(1);

    onClose.mockClear();
    fireEvent.click(screen.getByText('Dialog title'));
    expect(onClose).not.toHaveBeenCalled();
  });

  it('closeOnBackdropClick=false suppresses backdrop-click closing', () => {
    const onClose = vi.fn();
    render(
      <Dialog onClose={onClose} labelledBy="dlg-title" closeOnBackdropClick={false}>
        <Panel id="dlg-title" />
      </Dialog>,
    );
    fireEvent.click(screen.getByRole('dialog').parentElement!);
    expect(onClose).not.toHaveBeenCalled();
  });

  it('Escape closes the dialog by default, and can be suppressed', () => {
    const onClose = vi.fn();
    const { unmount } = render(
      <Dialog onClose={onClose} labelledBy="dlg-title">
        <Panel id="dlg-title" />
      </Dialog>,
    );
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
    unmount();

    const onCloseSuppressed = vi.fn();
    render(
      <Dialog onClose={onCloseSuppressed} labelledBy="dlg-title" closeOnEscape={false}>
        <Panel id="dlg-title" />
      </Dialog>,
    );
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onCloseSuppressed).not.toHaveBeenCalled();
  });

  it('focuses inside the panel on mount and restores focus to the trigger on close', async () => {
    const trigger = document.createElement('button');
    trigger.textContent = 'outside-trigger';
    document.body.appendChild(trigger);
    trigger.focus();
    expect(document.activeElement).toBe(trigger);

    const onClose = vi.fn();
    const { unmount } = render(
      <Dialog onClose={onClose} labelledBy="dlg-title">
        <Panel id="dlg-title" />
      </Dialog>,
    );
    expect(document.activeElement?.textContent).toBe('first');

    unmount();
    expect(document.activeElement).toBe(trigger);
    document.body.removeChild(trigger);
  });

  it('Tab loops focus within the panel and does not escape it', async () => {
    const user = userEvent.setup();
    render(
      <Dialog onClose={vi.fn()} labelledBy="dlg-title">
        <Panel id="dlg-title" extraButtonLabel="middle" />
      </Dialog>,
    );
    expect(document.activeElement?.textContent).toBe('first');
    await user.tab();
    expect(document.activeElement?.textContent).toBe('middle');
    await user.tab();
    expect(document.activeElement?.textContent).toBe('last');
    await user.tab();
    expect(document.activeElement?.textContent).toBe('first');
    await user.tab({ shift: true });
    expect(document.activeElement?.textContent).toBe('last');
  });

  describe('nested dialogs (SettingsModal opening LoginModal)', () => {
    it('Escape only closes the topmost (most recently mounted) dialog', () => {
      const onCloseOuter = vi.fn();
      const onCloseInner = vi.fn();
      const { rerender } = render(
        <>
          <Dialog onClose={onCloseOuter} labelledBy="outer-title">
            <Panel id="outer-title" />
          </Dialog>
        </>,
      );

      // Inner dialog mounts while the outer one is still mounted, simulating
      // SettingsModal staying open while it renders LoginModal on top.
      rerender(
        <>
          <Dialog onClose={onCloseOuter} labelledBy="outer-title">
            <Panel id="outer-title" />
          </Dialog>
          <Dialog onClose={onCloseInner} labelledBy="inner-title">
            <Panel id="inner-title" />
          </Dialog>
        </>,
      );

      fireEvent.keyDown(document, { key: 'Escape' });
      expect(onCloseInner).toHaveBeenCalledTimes(1);
      expect(onCloseOuter).not.toHaveBeenCalled();

      // Simulate the inner dialog actually closing (unmounting) after its
      // onClose fired -- the outer one should now become topmost.
      rerender(
        <>
          <Dialog onClose={onCloseOuter} labelledBy="outer-title">
            <Panel id="outer-title" />
          </Dialog>
        </>,
      );
      fireEvent.keyDown(document, { key: 'Escape' });
      expect(onCloseOuter).toHaveBeenCalledTimes(1);
    });

    it('backdrop click on the outer dialog does not fire while the inner one is on top', () => {
      const onCloseOuter = vi.fn();
      const onCloseInner = vi.fn();
      render(
        <>
          <Dialog onClose={onCloseOuter} labelledBy="outer-title">
            <Panel id="outer-title" />
          </Dialog>
          <Dialog onClose={onCloseInner} labelledBy="inner-title">
            <Panel id="inner-title" />
          </Dialog>
        </>,
      );

      const dialogs = screen.getAllByRole('dialog');
      // The outer dialog's own backdrop element is its parent -- clicking
      // the outer dialog's backdrop directly must not trigger its close
      // while a dialog is stacked on top of it.
      const outerBackdrop = dialogs[0].parentElement!;
      fireEvent.click(outerBackdrop);
      expect(onCloseOuter).not.toHaveBeenCalled();

      const innerBackdrop = dialogs[1].parentElement!;
      fireEvent.click(innerBackdrop);
      expect(onCloseInner).toHaveBeenCalledTimes(1);
    });

    it('Tab loops focus only within the topmost (inner) dialog panel', async () => {
      const user = userEvent.setup();
      render(
        <>
          <Dialog onClose={vi.fn()} labelledBy="outer-title">
            <Panel id="outer-title" extraButtonLabel="outer-middle" />
          </Dialog>
          <Dialog onClose={vi.fn()} labelledBy="inner-title">
            <Panel id="inner-title" extraButtonLabel="inner-middle" />
          </Dialog>
        </>,
      );

      // Initial focus lands in whichever dialog mounted last (the inner one).
      expect(document.activeElement?.textContent).toBe('first');
      await user.tab();
      expect(document.activeElement?.textContent).toBe('inner-middle');
      await user.tab();
      expect(document.activeElement?.textContent).toBe('last');
      await user.tab();
      // Loops back within the inner panel, never reaching the outer one's buttons.
      expect(document.activeElement?.textContent).toBe('first');
    });
  });
});
