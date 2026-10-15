// Screens call this after an action that changes the sidebar counts (reading mail, approving a
// review) so the badges update immediately instead of waiting for the next poll.
export const BADGE_REFRESH_EVENT = "oae:badges-refresh";

export function refreshBadges(): void {
  if (typeof window !== "undefined") window.dispatchEvent(new Event(BADGE_REFRESH_EVENT));
}
