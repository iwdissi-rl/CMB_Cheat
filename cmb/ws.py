"""Minimal RFC 6455 WebSocket client — stdlib only.

Enough for the Chrome DevTools Protocol: text frames, client-side masking
(required from clients), automatic pong replies to pings, continuation
reassembly, close handshake. No third-party dependencies.
"""

from __future__ import annotations

import base64
import os
import socket
import struct


class WS:
    def __init__(self, url: str, timeout: float = 30.0, *,
                 sock: socket.socket | None = None, pre_read: bytes = b""):
        """Open (or adopt) a socket to ``url`` and run the opening handshake.

        ``sock`` + ``pre_read`` are for sockets that were already connected and
        had the HTTP upgrade sent on the caller's behalf (CDP: the upgrade
        response can arrive while the caller polls the /json endpoint).
        """
        if not url.startswith("ws://"):
            raise ValueError(f"only ws:// URLs are supported, got {url!r}")
        hostport, _, path = url[len("ws://"):].partition("/")
        host, _, port = hostport.partition(":")
        self.sock = sock or socket.create_connection((host, int(port or 80)), timeout=timeout)
        self.sock.settimeout(timeout)
        self.buf = pre_read
        if sock is None:
            key = base64.b64encode(os.urandom(16)).decode()
            req = (
                f"GET /{path} HTTP/1.1\r\nHost: {hostport}\r\n"
                "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n"
                f"Origin: http://{hostport}\r\n\r\n"
            )
            self.sock.sendall(req.encode())
            head = self._recv_until_header()
            status = head.split("\r\n", 1)[0]
            if " 101" not in status:
                self.sock.close()
                raise ConnectionError(f"websocket handshake failed: {status!r}")

    # ---------- low-level ----------

    def _recvn(self, n: int) -> bytes:
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("websocket closed by peer")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def _recv_until_header(self) -> str:
        while b"\r\n\r\n" not in self.buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("websocket handshake: connection closed")
            self.buf += chunk
        head, self.buf = self.buf.split(b"\r\n\r\n", 1)
        return head.decode("latin-1")

    def _read_frame(self) -> tuple[int, int, bytes]:
        b1, b2 = self._recvn(2)
        fin, opcode = b1 & 0x80, b1 & 0x0F
        masked, ln = b2 & 0x80, b2 & 0x7F
        if ln == 126:
            ln = int.from_bytes(self._recvn(2), "big")
        elif ln == 127:
            ln = int.from_bytes(self._recvn(8), "big") & 0x7FFF_FFFF_FFFF_FFFF
        mask_key = self._recvn(4) if masked else b""
        payload = self._recvn(ln)
        if masked:
            payload = bytes(x ^ mask_key[i % 4] for i, x in enumerate(payload))
        return fin, opcode, payload

    # ---------- public ----------

    def send_frame(self, opcode: int, payload: bytes) -> None:
        head = bytearray([0x80 | opcode])
        n = len(payload)
        if n < 126:
            head.append(0x80 | n)
        elif n < 0x10000:
            head.append(0x80 | 126)
            head += struct.pack(">H", n)
        else:
            head.append(0x80 | 127)
            head += struct.pack(">Q", n)
        key = os.urandom(4)
        head += key
        head += bytes(x ^ key[i % 4] for i, x in enumerate(payload))
        self.sock.sendall(bytes(head))

    def send_text(self, s: str) -> None:
        self.send_frame(0x1, s.encode())

    def recv_text(self) -> str:
        """Return the next complete text message; pings are answered, pongs ignored."""
        while True:
            data = b""
            while True:
                fin, opcode, payload = self._read_frame()
                if opcode == 0x9:  # ping -> pong
                    self.send_frame(0xA, payload)
                    continue
                if opcode == 0xA:  # pong
                    continue
                if opcode == 0x8:
                    raise ConnectionError("websocket closed")
                if opcode in (0x1, 0x2, 0x0):
                    data += payload
                    if fin:
                        break
            return data.decode("utf-8", "replace")

    def close(self) -> None:
        try:
            self.send_frame(0x8, b"")
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass
