"""An echo server for gdh's network proxy tests, run as a companion: what comes in over TCP or UDP on PORT goes
back as it came.

    python3 echo.py PORT
"""
import asyncio
import sys

port = int(sys.argv[1])


class Echo(asyncio.DatagramProtocol):
    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        self.transport.sendto(data, addr)


async def echo(reader, writer):
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except ConnectionError:
        pass
    writer.close()


async def main():
    await asyncio.get_running_loop().create_datagram_endpoint(Echo, local_addr=("127.0.0.1", port))
    server = await asyncio.start_server(echo, "127.0.0.1", port)
    async with server:
        await server.serve_forever()


asyncio.run(main())
