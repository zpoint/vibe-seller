"""
In-process asyncio TCP relay for CDP (Chrome DevTools Protocol).

Ziniao browser assigns a random CDP port on each startBrowser call.
This proxy listens on a stable port and relays to the dynamic target,
so Playwright (via MCP) can always connect to the same address.

On WSL the target host is the Windows gateway IP, not 127.0.0.1.
"""

import asyncio
import logging

from app.config import LOCALHOST

logger = logging.getLogger(__name__)


class CDPTcpProxy:
    """Legacy async TCP relay: listen_port -> target_host:target_port.

    Single-client only. Kept as fallback for non-multiplexed scenarios.
    For multi-client CDP support, use CDPMuxProxy instead.
    """

    def __init__(
        self,
        listen_port: int,
        target_port: int,
        target_host: str = LOCALHOST,
    ):
        self.listen_port = listen_port
        self.target_port = target_port
        self.target_host = target_host
        self._server: asyncio.Server | None = None

    async def start(self):
        self._server = await asyncio.start_server(
            self._handle_client,
            LOCALHOST,
            self.listen_port,
        )
        logger.info(
            'CDP proxy listening: %s:%d -> %s:%d',
            LOCALHOST,
            self.listen_port,
            self.target_host,
            self.target_port,
        )

    async def stop(self):
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
            logger.info('CDP proxy stopped (port %d)', self.listen_port)

    async def _handle_client(
        self,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
    ):
        try:
            target_reader, target_writer = await asyncio.open_connection(
                self.target_host, self.target_port
            )
        except Exception as e:
            logger.warning(
                'CDP proxy: cannot connect to %s:%d: %s',
                self.target_host,
                self.target_port,
                e,
            )
            client_writer.close()
            return

        t1 = asyncio.create_task(self._relay(client_reader, target_writer))
        t2 = asyncio.create_task(self._relay(target_reader, client_writer))
        await asyncio.gather(t1, t2, return_exceptions=True)

    @staticmethod
    async def _relay(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ):
        try:
            while True:
                data = await reader.read(65536)
                if not data:
                    break
                writer.write(data)
                await writer.drain()
        except (
            ConnectionResetError,
            BrokenPipeError,
            asyncio.CancelledError,
        ):
            pass
        finally:
            writer.close()
