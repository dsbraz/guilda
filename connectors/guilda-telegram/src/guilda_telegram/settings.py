"""What the deploy configures for the bot, through the distribution: never hardcoded."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Settings:
    bot_token: str = field(repr=False)
    """The token BotFather gives; it never appears in a log or an error."""
    chat_id: int
    """The project's group, where the kernel's ``thread`` is."""
    thread: str
    topic_id: int | None = None
    """The forum topic of the group the thread is in, if the group has topics."""
