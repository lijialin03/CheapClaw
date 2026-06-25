(arg) => {
    const [selectors, filename] = arg;
    const textOf = (el) => (el && (el.innerText || el.textContent) || '').trim();
    if (selectors.file_card) {
        const cards = Array.from(document.querySelectorAll(selectors.file_card));
        if (cards.some((card) => textOf(card).includes(filename))) {
            return true;
        }
    }
    return !!(document.body.innerText && document.body.innerText.includes(filename));
}
