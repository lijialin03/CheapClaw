() => {
    const textOf = (el) => (el && (el.innerText || el.textContent) || '').replace(/\s+/g, ' ').trim();
    const visible = (el) => {
        if (!el) return false;
        const rect = el.getBoundingClientRect();
        const style = getComputedStyle(el);
        return rect.width > 0 && rect.height > 0 &&
            style.display !== 'none' &&
            style.visibility !== 'hidden' &&
            style.opacity !== '0';
    };
    const composers = Array.from(document.querySelectorAll('textarea[name="search"]'))
        .filter(visible);
    const authButtons = Array.from(document.querySelectorAll('button, a, [role="button"]'))
        .filter(visible)
        .map((el) => textOf(el))
        .filter(Boolean);
    return {
        href: location.href,
        hasComposer: composers.length > 0,
        authButtons,
    };
}
