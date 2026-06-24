() => {
    const visible = (el) => {
        if (!el) return false;
        const rect = el.getBoundingClientRect();
        const style = window.getComputedStyle(el);
        return rect.width > 0 && rect.height > 0 &&
            style.display !== 'none' &&
            style.visibility !== 'hidden' &&
            style.opacity !== '0';
    };
    const textOf = (el) => (el && (el.innerText || el.textContent) || '').replace(/\s+/g, ' ').trim();
    const attrText = (el) => [
        el.getAttribute('aria-label'),
        el.getAttribute('title'),
        el.getAttribute('data-testid'),
    ].filter(Boolean).join(' ');
    const buttons = Array.from(document.querySelectorAll('button, [role="button"], .send-button'))
        .filter(visible);
    return buttons.some((button) => {
        const labels = `${textOf(button)} ${attrText(button)}`;
        const hrefs = Array.from(button.querySelectorAll('use')).map((use) =>
            use.getAttribute('href') || use.getAttribute('xlink:href') || ''
        ).join(' ');
        const merged = `${labels} ${hrefs}`;
        return /停止|终止|stop|pause|icon-stop|icon-line-stop|square/i.test(merged) &&
            !/send|发送|arrow-up|icon-send/i.test(merged);
    });
}
