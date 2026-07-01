(selectors) => {
    const el = document.querySelector(selectors.composer);
    if (!el) return false;
    const value = el.value !== undefined ? el.value : (el.innerText || el.textContent || '');
    return value.trim() === '';
}
