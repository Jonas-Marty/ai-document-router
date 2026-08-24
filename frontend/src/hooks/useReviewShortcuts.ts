import { useEffect, useRef } from "react";

export interface ReviewShortcutHandlers {
  onApprove: () => void;
  onSkip: () => void;
  onOpenFolderPicker: () => void;
  onFocusName: () => void;
  onPrevPage: () => void;
  onNextPage: () => void;
  onOpenCheatSheet: () => void;
}

function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return (
    target.tagName === "INPUT" ||
    target.tagName === "TEXTAREA" ||
    target.tagName === "SELECT" ||
    target.isContentEditable
  );
}

/** SPEC 8.9, desktop only: Cmd/Ctrl+Enter approve, S skip, F folder picker, N focus name,
 * arrows page, ? cheat sheet. "Ignored while an input is focused, except the Cmd combo" --
 * the combo is checked before the typing-target guard, everything else after it, so typing
 * the letter "s" in the name field doesn't skip the document out from under the user.
 * `enabled` should be false whenever a dialog/sheet with its own focus trap is open (the
 * folder picker, the trash confirmation, the cheat sheet itself) so its own Escape/Enter
 * handling isn't fought by a global listener. */
export function useReviewShortcuts(enabled: boolean, handlers: ReviewShortcutHandlers) {
  const handlersRef = useRef(handlers);
  handlersRef.current = handlers;

  useEffect(() => {
    if (!enabled) return;

    function onKeyDown(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
        e.preventDefault();
        handlersRef.current.onApprove();
        return;
      }
      if (isTypingTarget(e.target)) return;

      switch (e.key) {
        case "s":
        case "S":
          handlersRef.current.onSkip();
          break;
        case "f":
        case "F":
          handlersRef.current.onOpenFolderPicker();
          break;
        case "n":
        case "N":
          handlersRef.current.onFocusName();
          break;
        case "ArrowLeft":
          handlersRef.current.onPrevPage();
          break;
        case "ArrowRight":
          handlersRef.current.onNextPage();
          break;
        case "?":
          handlersRef.current.onOpenCheatSheet();
          break;
        default:
          break;
      }
    }

    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [enabled]);
}
