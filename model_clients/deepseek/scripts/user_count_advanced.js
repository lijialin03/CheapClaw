(arg) => {
    const [selectors, previousCount] = arg;
    return document.querySelectorAll(selectors.user_message).length > previousCount;
}
