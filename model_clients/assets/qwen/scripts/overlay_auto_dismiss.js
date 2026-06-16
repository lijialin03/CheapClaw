() => {
    if (window.__qwenOverlayObserver) return;

    const visible = (el) => {
        if (!el) return false;
        const rect = el.getBoundingClientRect();
        const style = window.getComputedStyle(el);
        return rect.width > 0 && rect.height > 0 &&
            style.display !== 'none' &&
            style.visibility !== 'hidden' &&
            style.opacity !== '0';
    };
    const textOf = (el) => (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
    const clickByText = (root, patterns) => {
        for (const el of root.querySelectorAll('button,[role="button"],a')) {
            if (!visible(el)) continue;
            const text = textOf(el);
            if (patterns.some((pattern) => pattern.test(text))) {
                el.click();
                return true;
            }
        }
        return false;
    };
    const dismiss = () => {
        const overlays = document.querySelectorAll(
            '.qwen-modal-overlay, .ant-modal-root, .ant-modal-wrap, .ant-modal, [role="dialog"], [aria-modal="true"]'
        );
        let sawOverlay = false;
        for (const overlay of overlays) {
            if (!visible(overlay)) continue;
            sawOverlay = true;
            if (clickByText(overlay, [/继续/, /稍后/, /暂不/, /我知道/, /知道了/, /关闭/, /close/i, /cancel/i])) {
                return 'dismissed';
            }
            const dismissButton = overlay.querySelector('.ant-modal-close, .close, .qwen-modal-close');
            if (visible(dismissButton)) {
                dismissButton.click();
                return 'dismissed';
            }
        }
        return sawOverlay ? 'blocked' : 'none';
    };

    dismiss();
    window.__qwenOverlayObserver = new MutationObserver(() => {
        if (dismiss() === 'blocked') {
            document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}));
        }
    });
    window.__qwenOverlayObserver.observe(document.body, {childList: true, subtree: true});
}
