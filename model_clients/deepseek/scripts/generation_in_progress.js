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
    return Array.from(document.querySelectorAll('button, [role="button"], [aria-label], svg, .spinner, [class*="loading" i]'))
        .some((el) => {
            if (!visible(el)) return false;
            const label = `${textOf(el)} ${el.getAttribute('aria-label') || ''} ${el.className || ''}`;
            return /(stop|停止|generating|生成中|loading|加载|spinner)/i.test(label);
        });
}
