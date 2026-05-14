"""
WeCom (企业微信) channel (read-write): Self-built App bot.

Supports:
  - Receiving @mention messages (via callback URL webhook)
  - Actively sending messages to groups/users
  - Replying with text, images, files

Config keys:
  - corp_id: str         # 企业ID
  - corp_secret: str     # 应用Secret
  - agent_id: int        # 应用AgentId
  - token: str           # 回调Token (for receiving messages)
  - encoding_aes_key: str  # 回调EncodingAESKey
  - webhook_url: str     # 群机器人webhook URL (optional, for simple group bots)
"""

import logging

import httpx

from app.channels.base import ChannelMessage, ReadWriteChannel, register_channel

logger = logging.getLogger(__name__)


@register_channel
class WeComChannel(ReadWriteChannel):
    """WeCom Self-built App: receive mentions + active send."""

    channel_type = 'wecom'

    def __init__(self):
        self._config: dict = {}
        self._access_token: str = ''
        self._pending_messages: list[ChannelMessage] = []

    async def configure(self, config: dict) -> None:
        self._config = config
        if config.get('corp_id') and config.get('corp_secret'):
            await self._refresh_token()

    async def _refresh_token(self):
        """Get access token from WeCom API."""
        corp_id = self._config['corp_id']
        corp_secret = self._config['corp_secret']
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                'https://qyapi.weixin.qq.com/cgi-bin/gettoken',
                params={'corpid': corp_id, 'corpsecret': corp_secret},
            )
            data = resp.json()
            if data.get('errcode') == 0:
                self._access_token = data['access_token']
            else:
                logger.error(f'WeCom token error: {data}')

    def push_message(self, msg: ChannelMessage):
        """Called by webhook handler when a message is received."""
        self._pending_messages.append(msg)

    async def poll(self) -> list[ChannelMessage]:
        """Return accumulated messages from webhook callbacks."""
        messages = self._pending_messages[:]
        self._pending_messages.clear()
        return messages

    async def send(
        self,
        content: str,
        recipient: str = '',
        attachments: list[dict] | None = None,
    ) -> bool:
        """Send a message to a user or group.

        If webhook_url is configured (simple group bot), use that.
        Otherwise use the Self-built App API.
        """
        webhook_url = self._config.get('webhook_url')
        if webhook_url:
            return await self._send_webhook(webhook_url, content)

        if not self._access_token:
            await self._refresh_token()
            if not self._access_token:
                logger.error('WeCom: no access token')
                return False

        return await self._send_app_message(content, recipient)

    async def reply(
        self,
        original: ChannelMessage,
        content: str,
        attachments: list[dict] | None = None,
    ) -> bool:
        """Reply to a message (send to same recipient/group)."""
        recipient = original.raw.get('chat_id') or original.sender
        return await self.send(content, recipient, attachments)

    async def _send_webhook(self, webhook_url: str, content: str) -> bool:
        """Send via group bot webhook (simplest method)."""
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    webhook_url,
                    json={
                        'msgtype': 'text',
                        'text': {'content': content},
                    },
                )
                data = resp.json()
                if data.get('errcode') == 0:
                    return True
                logger.error(f'WeCom webhook error: {data}')
                return False
        except Exception as e:
            logger.error(f'WeCom webhook send error: {e}')
            return False

    async def _send_app_message(self, content: str, recipient: str) -> bool:
        """Send via Self-built App API."""
        try:
            agent_id = self._config.get('agent_id', 0)
            payload: dict = {
                'agentid': agent_id,
                'msgtype': 'text',
                'text': {'content': content},
            }

            # Determine recipient type
            if recipient.startswith('@'):
                # Chat ID (group)
                payload['chatid'] = recipient.lstrip('@')
            else:
                # User ID
                payload['touser'] = recipient or '@all'

            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    'https://qyapi.weixin.qq.com/cgi-bin/message/send',
                    params={'access_token': self._access_token},
                    json=payload,
                )
                data = resp.json()
                if data.get('errcode') == 0:
                    return True
                logger.error(f'WeCom app message error: {data}')
                return False
        except Exception as e:
            logger.error(f'WeCom send error: {e}')
            return False
