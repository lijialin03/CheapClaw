() => {
    const textOf = (el) => (el && (el.innerText || el.textContent) || '').replace(/\s+/g, ' ').trim();
    const cards = Array.from(document.querySelectorAll('div')).filter((el) => {
        const text = textOf(el);
        return /^回复\s*\d+/.test(text) && text.includes('我更喜欢这个回复');
    });
    const firstCard = cards[0];
    if (!firstCard) return {reply: '', clicked: false};

    const clone = firstCard.cloneNode(true);
    Array.from(clone.querySelectorAll('button, [role="button"]')).forEach((el) => el.remove());
    const reply = textOf(clone)
        .replace(/^回复\s*1\s*/, '')
        .replace(/已经完成思考\s*>?/g, '')
        .replace(/我更喜欢这个回复/g, '')
        .trim();

    const buttons = Array.from(firstCard.querySelectorAll('button, [role="button"]'));
    const preferButton = buttons.find((el) => textOf(el).includes('我更喜欢这个回复'));
    if (preferButton) {
        preferButton.click();
        return {reply, clicked: true};
    }
    return {reply, clicked: false};
}
