import { useSyncExternalStore } from "react";

// A store that never changes: the only signal wanted is server vs. client.
const subscribeNever = () => () => {};
const whileHydrated = () => true;
const beforeHydration = () => false;

/**
 * True once React has attached to the server-rendered markup.
 *
 * Server-rendered controls are inert until then — a press lands on real
 * buttons that do nothing — so a route that must be pressed straight after
 * paint says when that moment has passed (`data-interactive`), and the browser
 * tests wait for the attribute instead of racing the first paint. The server
 * snapshot is what makes this a hydration signal rather than a mount effect:
 * the markup ships `false`, and the first client render flips it.
 */
export function useHydrated(): boolean {
  return useSyncExternalStore(subscribeNever, whileHydrated, beforeHydration);
}
