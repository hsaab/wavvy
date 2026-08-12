export function resolveLinksLabel({ resolvingLinks, selectedCount, resolveProgress }) {
  if (resolvingLinks) {
    if (resolveProgress) {
      return `Resolving ${resolveProgress.current}/${resolveProgress.total}…`;
    }
    return "Resolving…";
  }
  if (selectedCount > 0) {
    return `Resolve Links (${selectedCount})`;
  }
  return "Resolve Links";
}

export function resolveLinksDisabled({ resolvingLinks }) {
  return Boolean(resolvingLinks);
}

export function cartButtonLabel({ store, cartStarting, isCartRunning }) {
  if (cartStarting === store || isCartRunning) {
    return "Carting…";
  }
  return store === "traxsource" ? "Cart TS" : "Cart BP";
}

export function cartButtonDisabled({ cartStarting, isCartRunning }) {
  return cartStarting !== null || Boolean(isCartRunning);
}
