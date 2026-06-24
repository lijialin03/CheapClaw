() => {
    const replies = document.querySelectorAll('.qwen-chat-message-assistant .response-message-content');
    if (!replies.length) return '';
    return replies[replies.length - 1].textContent.trim();
}
