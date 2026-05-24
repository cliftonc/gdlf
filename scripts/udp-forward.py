#!/usr/bin/env python3
"""Minimal bidirectional UDP forwarder.

Listens on LISTEN_ADDR and relays each client's traffic to TARGET_ADDR,
maintaining per-source-port state so server replies are returned to the
right client. Used to bridge LAN -> Colima VM, where Colima only forwards
TCP by default.

  ./udp-forward.py 0.0.0.0:51820 192.168.64.2:51820
"""
from __future__ import annotations

import socket
import sys
import threading
import time


def parse(addr: str) -> tuple[str, int]:
    host, port = addr.rsplit(":", 1)
    return host, int(port)


def main() -> int:
    if len(sys.argv) != 3:
        print(f"usage: {sys.argv[0]} LISTEN_ADDR TARGET_ADDR", file=sys.stderr)
        return 2

    listen_addr = parse(sys.argv[1])
    target_addr = parse(sys.argv[2])

    listen_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    listen_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listen_sock.bind(listen_addr)
    print(f"[udp-forward] listening on {listen_addr[0]}:{listen_addr[1]} -> "
          f"{target_addr[0]}:{target_addr[1]}", flush=True)

    # client_addr -> (per-client-sock, last_seen)
    clients: dict[tuple[str, int], tuple[socket.socket, float]] = {}
    lock = threading.Lock()

    def server_reader(server_sock: socket.socket, client_addr: tuple[str, int]) -> None:
        while True:
            try:
                data, _ = server_sock.recvfrom(65535)
            except OSError:
                return
            try:
                listen_sock.sendto(data, client_addr)
            except OSError:
                return
            with lock:
                if client_addr in clients:
                    clients[client_addr] = (server_sock, time.time())

    def reaper() -> None:
        while True:
            time.sleep(60)
            cutoff = time.time() - 300
            with lock:
                stale = [a for a, (_, t) in clients.items() if t < cutoff]
                for a in stale:
                    s, _ = clients.pop(a)
                    s.close()
            if stale:
                print(f"[udp-forward] reaped {len(stale)} idle clients", flush=True)

    threading.Thread(target=reaper, daemon=True).start()

    while True:
        try:
            data, client_addr = listen_sock.recvfrom(65535)
        except KeyboardInterrupt:
            return 0
        with lock:
            entry = clients.get(client_addr)
            if entry is None:
                server_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                server_sock.connect(target_addr)
                clients[client_addr] = (server_sock, time.time())
                threading.Thread(
                    target=server_reader, args=(server_sock, client_addr), daemon=True
                ).start()
            else:
                server_sock = entry[0]
                clients[client_addr] = (server_sock, time.time())
        try:
            server_sock.send(data)
        except OSError as e:
            print(f"[udp-forward] send to {target_addr} failed: {e}", flush=True)


if __name__ == "__main__":
    sys.exit(main() or 0)
