"""
Channel abstraction for message sources (email, WeCom, Slack, etc.).

Two base classes:
  - BaseChannel: read-only (can receive messages)
  - ReadWriteChannel: can both receive and send messages

To add a new channel:
  1. Create app/channels/mybackend.py
  2. Subclass BaseChannel or ReadWriteChannel
  3. Register in CHANNEL_REGISTRY below
  4. See DEVELOPER.md for full guide
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class ChannelMessage:
    """A message received from or sent to a channel."""

    channel_type: str  # "email", "wecom", "slack", etc.
    sender: str  # email address, user id, etc.
    content: str  # message body text
    subject: str = ''  # email subject, group name, etc.
    attachments: list[dict] = field(default_factory=list)  # [{name, url, type}]
    raw: dict = field(default_factory=dict)  # original raw data
    message_id: str = ''  # external message ID


class BaseChannel(ABC):
    """Read-only channel: can receive/poll messages but not send."""

    channel_type: str = 'base'

    @abstractmethod
    async def configure(self, config: dict) -> None:
        """Set up the channel with credentials/settings."""
        ...

    @abstractmethod
    async def poll(self) -> list[ChannelMessage]:
        """Poll for new messages. Returns list of new messages since last poll."""
        ...

    async def close(self) -> None:
        """Clean up resources. Override if your channel needs cleanup."""
        return


class ReadWriteChannel(BaseChannel):
    """Bidirectional channel: can both receive and send messages."""

    @abstractmethod
    async def send(
        self,
        content: str,
        recipient: str = '',
        attachments: list[dict] | None = None,
    ) -> bool:
        """Send a message. Returns True on success."""
        ...

    @abstractmethod
    async def reply(
        self,
        original: ChannelMessage,
        content: str,
        attachments: list[dict] | None = None,
    ) -> bool:
        """Reply to a received message. Returns True on success."""
        ...


# Channel registry: maps channel_type -> class
CHANNEL_REGISTRY: dict[str, type[BaseChannel]] = {}


def register_channel(cls: type[BaseChannel]) -> type[BaseChannel]:
    """Decorator to register a channel implementation."""
    CHANNEL_REGISTRY[cls.channel_type] = cls
    return cls


def get_channel(channel_type: str) -> BaseChannel:
    """Create a channel instance by type."""
    cls = CHANNEL_REGISTRY.get(channel_type)
    if not cls:
        raise ValueError(
            f'Unknown channel type: {channel_type}. Available: {list(CHANNEL_REGISTRY.keys())}'
        )
    return cls()
