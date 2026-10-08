// "Is the user inside the workbench" - the single site-wide criterion.
//
// The workbench has two shells: customer-side /app/* (userNav) and ops-side /ops/* (opsNav).
// Both are admin layouts where "the page does not scroll and the bottom right is occupied by table actions", so the marketing floaters at the bottom right
// (Book a demo, GEO marketing specialist) should collapse into edge handles in both places instead of covering the action area.
//
// Why extract it: this criterion used to have three separate copies, and **two of them missed /app** -
//   App.tsx          shell  = /app || /ops   ✓
//   ContactModal     docked = /ops           ✗ in the customer workbench "Book a demo" was always the big pill with text
//   AgentChatWidget  inOps  = /ops           ✗ in the customer workbench the 15s collapse timer never started
// Three copies of the same thing with one differing is exactly the shape of the planRow.addDays bug. Keep only this one.
export const isWorkbenchPath = (pathname: string): boolean =>
  // /app/diagnosis has been moved out of the workbench into a standalone page; it does not use the sidebar shell / floater-collapsing logic.
  (pathname.startsWith('/app') && pathname !== '/app/diagnosis')
  || pathname.startsWith('/ops');
