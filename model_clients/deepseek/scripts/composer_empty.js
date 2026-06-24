() => {
    const el = document.querySelector('textarea[name="search"]');
    if (!el) return false;
    const value = el.value !== undefined ? el.value : (el.innerText || el.textContent || '');
    return value.trim() === '';
}
