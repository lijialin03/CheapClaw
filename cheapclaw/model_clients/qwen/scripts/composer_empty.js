(selectors) => {
    const textarea = document.querySelector(selectors.composer);
    return textarea && textarea.value === '';
}
