(selectors) => {
    const visible = (el) => {
        if (!el) return false;
        const rect = el.getBoundingClientRect();
        const style = getComputedStyle(el);
        return rect.width > 0 && rect.height > 0 &&
            style.display !== 'none' &&
            style.visibility !== 'hidden' &&
            style.opacity !== '0';
    };
    const keywords = selectors.generation_stop_keywords || [];
    const pattern = new RegExp(keywords.join('|'), 'i');
    return Array.from(document.querySelectorAll('button, [role="button"], [aria-label], svg, .spinner, [class*="loading" i]'))
        .some((el) => {
            if (!visible(el)) return false;
            const textOf = (el) => (el && (el.innerText || el.textContent) || '').replace(/\s+/g, ' ').trim();
            const label = `${textOf(el)} ${el.getAttribute('aria-label') || ''} ${el.className || ''}`;
            return pattern.test(label);
        });
}
