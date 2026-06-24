() => {
    const nodes = Array.from(document.querySelectorAll('.ds-assistant-message-main-content'))
        .filter((el) => (el.innerText || el.textContent || '').trim());
    const keys = nodes
        .map((el) => el.closest('[data-virtual-list-item-key]'))
        .filter(Boolean)
        .map((el) => Number(el.getAttribute('data-virtual-list-item-key')))
        .filter(Number.isFinite);
    if (keys.length > 0) return Math.max(...keys);
    return nodes.length;
}
