"""A barrier for games stepped together, run as a gdh companion: each game
sends a byte a frame, and once every one of the N has, each is answered with a
byte and the next frame can start. It's how a server on a test clock that ticks
when every client has asked holds its clients together.

    python3 barrier.py PORT N
"""
import select
import socket
import sys

port, count = int(sys.argv[1]), int(sys.argv[2])
server = socket.socket()
server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server.bind(("127.0.0.1", port))
server.listen()
games = []
asked = set()
while True:
    readable, _, _ = select.select([server, *games], [], [])
    for s in readable:
        if s is server:
            game, _ = server.accept()
            game.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            games.append(game)
        elif s.recv(64):
            asked.add(s)
        else:
            games.remove(s)
            asked.discard(s)
    if len(games) == count and asked == set(games):
        for game in games:
            game.sendall(b"\x01")
        asked.clear()
