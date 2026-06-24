() => {
    const visible = (el) => {
        if (!el) return false;
        const rect = el.getBoundingClientRect();
        const style = getComputedStyle(el);
        return rect.width > 0 && rect.height > 0 &&
            style.display !== 'none' &&
            style.visibility !== 'hidden' &&
            style.opacity !== '0';
    };
    const nodes = Array.from(document.querySelectorAll('.ds-assistant-message-main-content'))
        .filter(visible)
        .filter((el) => (el.innerText || el.textContent || '').trim());
    const node = nodes[nodes.length - 1];
    if (!node) return '';
    const clone = node.cloneNode(true);
    clone.querySelectorAll('.ds-markdown-cite').forEach((el) => el.remove());
    return (clone.innerText || clone.textContent || '').trim();
}
