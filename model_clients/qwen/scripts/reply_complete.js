() => {
    const assistants = Array.from(document.querySelectorAll('.qwen-chat-message-assistant'))
        .filter((message) => message.querySelector('.response-message-content'));
    const latestAssistant = assistants[assistants.length - 1];
    return Boolean(latestAssistant?.querySelector('.message-hoc-container'));
}
