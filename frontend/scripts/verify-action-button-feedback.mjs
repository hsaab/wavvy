import assert from "node:assert/strict";
import {
  resolveLinksLabel,
  resolveLinksDisabled,
  cartButtonLabel,
  cartButtonDisabled,
  shouldIgnoreCartClick,
} from "../src/components/actionButtonFeedback.js";

function expectResolveLabel(name, ctx, expected) {
  const actual = resolveLinksLabel(ctx);
  assert.equal(actual, expected, name);
}

function expectResolveDisabled(name, ctx, expected) {
  const actual = resolveLinksDisabled(ctx);
  assert.equal(actual, expected, name);
}

function expectCartLabel(name, ctx, expected) {
  const actual = cartButtonLabel(ctx);
  assert.equal(actual, expected, name);
}

function expectCartDisabled(name, ctx, expected) {
  const actual = cartButtonDisabled(ctx);
  assert.equal(actual, expected, name);
}

function expectIgnoreCartClick(name, ctx, expected) {
  const actual = shouldIgnoreCartClick(ctx);
  assert.equal(actual, expected, name);
}

// Idle / empty toolbar
expectResolveLabel(
  "Idle empty queue: Resolve Links shows the default label",
  { resolvingLinks: false, selectedCount: 0, resolveProgress: null },
  "Resolve Links",
);
expectResolveDisabled(
  "Idle empty queue: Resolve Links is enabled",
  { resolvingLinks: false },
  false,
);
expectCartLabel(
  "Idle empty queue: Cart BP shows the default label",
  { store: "beatport", cartStarting: null, isCartRunning: false },
  "Cart BP",
);
expectCartLabel(
  "Idle empty queue: Cart TS shows the default label",
  { store: "traxsource", cartStarting: null, isCartRunning: false },
  "Cart TS",
);
expectCartDisabled(
  "Idle empty queue: cart buttons are enabled",
  { cartStarting: null, isCartRunning: false },
  false,
);

// Idle with a selection (today's TrackQueue label)
expectResolveLabel(
  "Idle with a selection: Resolve Links (N) matches today's TrackQueue label",
  { resolvingLinks: false, selectedCount: 4, resolveProgress: null },
  "Resolve Links (4)",
);

// Click Resolve Links with no selection: Resolving… and disabled until POST finishes
expectResolveLabel(
  "Click Resolve Links with no selection: button shows Resolving…",
  { resolvingLinks: true, selectedCount: 0, resolveProgress: null },
  "Resolving…",
);
expectResolveDisabled(
  "Click Resolve Links with no selection: button is disabled until the POST finishes",
  { resolvingLinks: true },
  true,
);

// Click Resolve Links with a selection: Resolving… or Resolving n/m…
expectResolveLabel(
  "Click Resolve Links with a selection and no progress: button shows Resolving…",
  { resolvingLinks: true, selectedCount: 12, resolveProgress: null },
  "Resolving…",
);
expectResolveLabel(
  "Click Resolve Links with a selection and progress: button shows Resolving current/total…",
  {
    resolvingLinks: true,
    selectedCount: 12,
    resolveProgress: { current: 3, total: 12 },
  },
  "Resolving 3/12…",
);
expectResolveDisabled(
  "Click Resolve Links with a selection: button is disabled while resolving",
  { resolvingLinks: true },
  true,
);

// Resolve POST fails: re-enable; label returns to idle (error banner stays in TrackQueue)
expectResolveDisabled(
  "Resolve POST failed: resolvingLinks false means the button is enabled again",
  { resolvingLinks: false },
  false,
);
expectResolveLabel(
  "Resolve POST failed: label returns to Resolve Links",
  { resolvingLinks: false, selectedCount: 0, resolveProgress: null },
  "Resolve Links",
);
expectResolveLabel(
  "Resolve POST failed with a selection still checked: label returns to Resolve Links (N)",
  { resolvingLinks: false, selectedCount: 4, resolveProgress: null },
  "Resolve Links (4)",
);

// Click Cart BP: Carting… immediately, before any WS event; both cart buttons disabled
expectCartLabel(
  "Click Cart BP before any WS event: Cart BP shows Carting…",
  { store: "beatport", cartStarting: "beatport", isCartRunning: false },
  "Carting…",
);
expectCartLabel(
  "Click Cart BP before any WS event: Cart TS stays Cart TS, not Carting…",
  { store: "traxsource", cartStarting: "beatport", isCartRunning: false },
  "Cart TS",
);
expectCartDisabled(
  "Click Cart BP before any WS event: both cart buttons are disabled",
  { cartStarting: "beatport", isCartRunning: false },
  true,
);

// Click Cart TS: Carting… on TS, both disabled, BP stays Cart BP
expectCartLabel(
  "Click Cart TS before any WS event: Cart TS shows Carting…",
  { store: "traxsource", cartStarting: "traxsource", isCartRunning: false },
  "Carting…",
);
expectCartLabel(
  "Click Cart TS before any WS event: Cart BP stays Cart BP, not Carting…",
  { store: "beatport", cartStarting: "traxsource", isCartRunning: false },
  "Cart BP",
);
expectCartDisabled(
  "Click Cart TS before any WS event: both cart buttons are disabled",
  { cartStarting: "traxsource", isCartRunning: false },
  true,
);

// cart_started: local cartStarting may be null; Carting… remains via isCartRunning
expectCartLabel(
  "Cart WS cart_started: cartStarting cleared, Cart BP stays Carting… via isCartRunning",
  { store: "beatport", cartStarting: null, isCartRunning: true },
  "Carting…",
);
expectCartLabel(
  "Cart WS cart_started: isCartRunning also shows Carting… on Cart TS",
  { store: "traxsource", cartStarting: null, isCartRunning: true },
  "Carting…",
);
expectCartDisabled(
  "Cart WS cart_started: both cart buttons stay disabled via isCartRunning",
  { cartStarting: null, isCartRunning: true },
  true,
);

// Idle queue: a Cart BP click is not ignored so the first click can fire
expectIgnoreCartClick(
  "Idle queue: a Cart BP click is not ignored so the first click can fire",
  { cartInFlight: null, isCartRunning: false },
  false,
);

// Click Cart BP, then spam Cart BP before any WebSocket event
expectIgnoreCartClick(
  "Click Cart BP then spam Cart BP before any WS event: ignored because cartInFlight is beatport",
  { cartInFlight: "beatport", isCartRunning: false },
  true,
);

// Click Cart BP, then spam Cart TS in the same moment (shared in-flight lock)
expectIgnoreCartClick(
  "Click Cart BP then spam Cart TS in the same moment: ignored because cartInFlight is already beatport",
  { cartInFlight: "beatport", isCartRunning: false },
  true,
);

// Click Cart TS, then spam
expectIgnoreCartClick(
  "Click Cart TS then spam: ignored because cartInFlight is traxsource",
  { cartInFlight: "traxsource", isCartRunning: false },
  true,
);

// After cart_started: still ignored so a second POST cannot start while Playwright is running
expectIgnoreCartClick(
  "After cart_started: still ignored so a second POST cannot start while Playwright is running",
  { cartInFlight: null, isCartRunning: true },
  true,
);

// After cart_complete: not ignored, buttons can be used again
expectIgnoreCartClick(
  "After cart_complete: not ignored, buttons can be used again",
  { cartInFlight: null, isCartRunning: false },
  false,
);

// After cart_error or a failed POST (same idle shape): not ignored
expectIgnoreCartClick(
  "After cart_error or a failed POST: not ignored",
  { cartInFlight: null, isCartRunning: false },
  false,
);

console.log("verify-action-button-feedback: all cases passed");
