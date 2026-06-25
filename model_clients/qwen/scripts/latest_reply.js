(selectors) => {
    const replies = document.querySelectorAll(selectors.reply_content);
    if (!replies.length) return '';
    return replies[replies.length - 1].textContent.trim();
}
