/** A rendered host element, as Testing Library returns it. */
type HostElement = { props: Record<string, unknown>; parent: HostElement | null };

/**
 * The top edge mode of the nearest SafeAreaView around `element`: 'additive' or 'maximum' when
 * the screen keeps clear of the status bar and notch, 'off' when it doesn't, null when there is
 * no SafeAreaView at all. Test-only (BEAV-201): every screen hides the stack header, so its own
 * SafeAreaView must take the top inset.
 */
export function topSafeAreaEdge(element: HostElement): string | null {
  for (let node: HostElement | null = element; node; node = node.parent) {
    const edges = node.props.edges as Record<string, string> | undefined;
    if (edges && typeof edges === 'object') return edges.top ?? 'off';
  }
  return null;
}
