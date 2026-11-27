import { Brand } from "./Brand";
import { cloneElement, isValidElement, useEffect, useRef, type ReactElement, type ReactNode } from "react";

/** The id of a field's error message, for aria-describedby. */
export const errorId = (id: string) => `${id}-error`;

/** aria-invalid and aria-describedby for an input whose error Field shows (WCAG 3.3.1, 4.1.2). */
export function invalidProps(id: string, error?: string, describedBy?: string) {
  const ids = [describedBy, error ? errorId(id) : undefined].filter(Boolean).join(" ");
  return { "aria-invalid": error ? true : undefined, "aria-describedby": ids || undefined } as const;
}

export function Field({ label, error, id, children }: { label: string; error?: string; id: string; children: ReactNode }) {
  // A plain <input> child is linked to its error here; a wrapped input passes invalidProps itself.
  const child = isValidElement(children) && children.type === "input"
    ? cloneElement(children as ReactElement<Record<string, unknown>>,
        invalidProps(id, error, (children.props as { "aria-describedby"?: string })["aria-describedby"]))
    : children;
  return (
    <div className={`field${error ? " has-error" : ""}`}>
      <label htmlFor={id}>{label}</label>
      {child}
      {error && <span className="error" id={errorId(id)} role="alert">{error}</span>}
    </div>
  );
}

/** After a submit with errors, move focus to the first field that has one (WCAG 3.3.1), on
 *  every form of these pages. Runs after React has drawn the errors. */
export function focusFirstErrorOnSubmit(doc: Document = document) {
  doc.addEventListener("submit", (ev) => {
    const form = ev.target as HTMLElement | null;
    window.setTimeout(() => form?.querySelector<HTMLElement>('[aria-invalid="true"]')?.focus(), 0);
  });
}

/** Move focus to the heading of a new step when the step changes, not on first load, so
 *  keyboard and screen-reader users land on (and hear) what changed (WCAG 2.4.3, 4.1.3). */
export function useFocusHeadingOnChange(key: string) {
  const ref = useRef<HTMLElement>(null);
  const first = useRef(key);
  useEffect(() => {
    if (key === first.current) return;
    first.current = "";
    const h = ref.current?.querySelector<HTMLElement>("h1");
    if (h) {
      h.tabIndex = -1;
      h.focus();
    }
  }, [key]);
  return ref;
}

export function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="row">
      <dt>{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}


export function Shell({ status, livemode, children }: { status: string; livemode?: boolean; children: ReactNode }) {
  return (
    <>
      <header className="topbar">
        <Brand livemode={livemode} />
        <span className="topbar-status">{status}</span>
      </header>
      <main className="layout">{children}</main>
    </>
  );
}
