import type { ReactNode } from 'react';
import {
  createContext,
  useCallback,
  useContext,
  useId,
  useLayoutEffect,
  useMemo,
  useState,
} from 'react';

/*
 * The heading-to-body hierarchy at large text sizes (BEAV-223). A heading stops growing where
 * its own longest word fits (BEAV-219); without a link between the two, body text beside it
 * kept growing to twice its size and read bigger than the heading (BEAV-215 review). Headings
 * register how far they grow with the enclosing scope, and the body text in it is capped at
 * the scale of the tightest heading or, when that heading is bigger by design, at the
 * heading's rendered size.
 */

/** What a heading tells its scope: the most it grows, and the size it grows from. */
export interface HeadingScale {
  /** The heading's `maxFontSizeMultiplier`, never below 1. */
  scale: number;
  fontSize: number;
}

interface Scope {
  headings: readonly HeadingScale[];
  register(id: string, heading: HeadingScale): () => void;
}

const ScopeContext = createContext<Scope | null>(null);

/**
 * The text scale cap headings set for text of `fontSize`: the tightest heading's scale, or its
 * rendered size where it is bigger by design. Infinity without headings.
 */
export function headingCap(headings: readonly HeadingScale[], fontSize: number): number {
  let cap = Infinity;
  for (const heading of headings) {
    cap = Math.min(cap, heading.scale * Math.max(1, heading.fontSize / fontSize));
  }
  return cap;
}

/**
 * The text a screen shows together, such as a screen body: the headings inside it register
 * how far they grow, and its body text grows no further (see `headingCap`).
 */
export function TextScaleScope({ children }: { children: ReactNode }) {
  const [entries, setEntries] = useState<ReadonlyMap<string, HeadingScale>>(new Map());
  const register = useCallback((id: string, heading: HeadingScale) => {
    setEntries((current) => {
      const known = current.get(id);
      if (known?.scale === heading.scale && known.fontSize === heading.fontSize) return current;
      return new Map(current).set(id, heading);
    });
    return () =>
      setEntries((current) => {
        if (!current.has(id)) return current;
        const next = new Map(current);
        next.delete(id);
        return next;
      });
  }, []);
  const value = useMemo(() => ({ headings: [...entries.values()], register }), [entries, register]);
  return <ScopeContext.Provider value={value}>{children}</ScopeContext.Provider>;
}

/** Registers a heading's scale with the enclosing `TextScaleScope`, if any. */
export function useRegisterHeading(scale: number, fontSize: number): void {
  const scope = useContext(ScopeContext);
  const id = useId();
  const register = scope?.register;
  // A layout effect: the body text is re-capped before the first frame is drawn.
  useLayoutEffect(
    () => (register ? register(id, { scale, fontSize }) : undefined),
    [register, id, scale, fontSize],
  );
}

/** The text scale cap the headings in scope set for text of `fontSize` (see `headingCap`). */
export function useHeadingCap(fontSize: number): number {
  const scope = useContext(ScopeContext);
  return scope ? headingCap(scope.headings, fontSize) : Infinity;
}
