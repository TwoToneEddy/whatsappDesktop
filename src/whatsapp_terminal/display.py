import emoji

from .store import safe_text


def display_text(value, show_emoji=False):
    if not show_emoji:
        return safe_text(emoji.demojize(str(value)))
    return "\u200d".join(safe_text(part) for part in str(value).split("\u200d"))
