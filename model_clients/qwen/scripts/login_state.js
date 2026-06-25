async (selectors) => {
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
    const authButtons = Array.from(document.querySelectorAll('button, a')).filter((el) => {
        const text = textOf(el);
        return visible(el) && /^(Log in|Sign up|登录|注册)$/i.test(text);
    }).map((el) => textOf(el));
    let authStatus = null;
    try {
        const resp = await fetch('/api/v1/auths/', {credentials: 'include'});
        authStatus = resp.status;
    } catch (error) {
        authStatus = `error:${error && error.message ? error.message : error}`;
    }
    return {authButtons, authStatus};
}
