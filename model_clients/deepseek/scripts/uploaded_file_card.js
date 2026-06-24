(filename) => {
    const textOf = (el) => (el && (el.innerText || el.textContent) || '').trim();
    const cards = Array.from(document.querySelectorAll('.ds-animated-size-item'));
    if (cards.some((card) => textOf(card).includes(filename))) {
        return true;
    }
    return !!(document.body.innerText && document.body.innerText.includes(filename));
}
