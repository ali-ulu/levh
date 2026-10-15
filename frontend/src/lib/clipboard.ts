/**
 * Copy text to the clipboard, working on plain-HTTP origins too.
 *
 * `navigator.clipboard` only exists in a secure context (HTTPS or localhost).
 * A LEVH server reached over the LAN — `http://192.168.x.x:8000`, the common
 * local-first setup — has no `navigator.clipboard` at all, and even when the
 * API is present the browser can reject `writeText` without a user gesture
 * (`NotAllowedError`). Both cases used to make every "Copy" button throw and
 * silently do nothing.
 *
 * Returns true when the text made it to the clipboard, false otherwise, so
 * callers can show the right confirmation instead of claiming success.
 */
export async function copyText(text: string): Promise<boolean> {
  const clipboard = typeof navigator !== "undefined" ? navigator.clipboard : undefined;
  if (clipboard && typeof clipboard.writeText === "function") {
    try {
      await clipboard.writeText(text);
      return true;
    } catch {
      // Fall through to the legacy path below.
    }
  }
  return copyTextFallback(text);
}

function copyTextFallback(text: string): boolean {
  if (typeof document === "undefined" || typeof document.execCommand !== "function") {
    return false;
  }
  const textarea = document.createElement("textarea");
  textarea.value = text;
  // Off-screen and read-only: keeps the selection from scrolling or being
  // edited, and mobile Safari refuses to select an element it can scroll to.
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.top = "-1000px";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);
  try {
    textarea.select();
    return document.execCommand("copy");
  } catch {
    return false;
  } finally {
    document.body.removeChild(textarea);
  }
}
