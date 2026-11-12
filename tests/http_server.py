# -*- coding: utf-8 -*-

"""A small real HTTP server used to exercise lassie against actual sockets.

The mock-based tests patch the retrieval methods, so they never exercise
streaming, gzip decompression, chunked transfer encoding or redirect
handling. This module serves preconfigured responses from a background
thread on localhost, letting the ``max_bytes`` budget tests observe the
bytes lassie actually pulls off the wire.
"""

import gzip
import socket
import threading
from http.server import BaseHTTPRequestHandler
try:
    from http.server import ThreadingHTTPServer
except ImportError:  # Python < 3.7
    from http.server import HTTPServer
    from socketserver import ThreadingMixIn

    class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
        daemon_threads = True
from urllib.parse import urlparse


class _Handler(BaseHTTPRequestHandler):
    # Routes are installed per-test on the server instance.
    # HTTP/1.1 is required for Transfer-Encoding: chunked to be preserved;
    # the default HTTP/1.0 protocol version strips it.
    protocol_version = 'HTTP/1.1'

    def log_message(self, format, *args):  # noqa: A002 - stdlib signature
        pass

    def _close_after_response(self):
        # Disable keep-alive so each handler thread ends when the response is
        # sent; otherwise a pooled idle client connection would block it.
        self.close_connection = True
        self.send_header('Connection', 'close')

    @property
    def _path(self):
        # Strip the query string (oEmbed requests append ?url=...).
        return urlparse(self.path).path

    def _route(self):
        return self.server.routes.get(self._path)

    def do_HEAD(self):
        route = self._route()
        if route is None:
            self.send_response(404)
            self._close_after_response()
            self.end_headers()
            return

        self.send_response(route.get('status', 200))
        self._close_after_response()
        for key, value in route.get('headers', {}).items():
            self.send_header(key, value)
        self.end_headers()

    def do_GET(self):
        route = self._route()
        self.server.requests.append(self._path)
        self.server.request_headers.append(dict(self.headers))
        if route is None:
            self.send_response(404)
            self._close_after_response()
            self.end_headers()
            return

        if 'redirect' in route:
            self.send_response(route.get('status', 302))
            self._close_after_response()
            self.send_header('Location', route['redirect'])
            # A body on a redirect response: lassie must account for these
            # bytes even though it follows the Location header.
            body = route.get('body', b'')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)
            return

        body = route['body']
        self.send_response(route.get('status', 200))
        self._close_after_response()
        for key, value in route.get('headers', {}).items():
            self.send_header(key, value)

        if route.get('gzip'):
            body = gzip.compress(body)
            self.send_header('Content-Encoding', 'gzip')

        if route.get('chunked'):
            # No Content-Length: chunked transfer encoding is the framing.
            self.send_header('Transfer-Encoding', 'chunked')
            self.end_headers()
            # Emit several chunks so streaming iter_content has work to do.
            step = max(1, len(body) // 4)
            for index in range(0, len(body), step):
                piece = body[index:index + step]
                self.wfile.write(('%x\r\n' % len(piece)).encode('ascii'))
                self.wfile.write(piece)
                self.wfile.write(b'\r\n')
            self.wfile.write(b'0\r\n\r\n')
            return

        if 'Content-Length' not in dict(route.get('headers', {})):
            self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class LocalHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self):
        ThreadingHTTPServer.__init__(self, ('127.0.0.1', 0), _Handler)
        self.routes = {}
        self.requests = []
        self.request_headers = []
        self._raw_sockets = []
        self._thread = threading.Thread(target=self.serve_forever)
        self._thread.daemon = True
        self._thread.start()

    @property
    def base_url(self):
        return 'http://127.0.0.1:%s' % self.server_address[1]

    def url(self, path):
        return self.base_url + path

    def set_route(self, path, **route):
        self.routes[path] = route

    def serve_raw_on_close(self, path, headers, body):
        """Serve one raw response with no reliable framing, then disconnect.

        Used to prove lassie trusts bytes it actually reads rather than a
        ``Content-Length`` header. The supplied headers are sent verbatim
        (typically an understated ``Content-Length``) followed by the full
        body, and the connection is closed so the client reads to EOF.
        """
        def handle(listener):
            client, _ = listener.accept()
            try:
                client.recv(4096)
                client.sendall(
                    b'HTTP/1.1 200 OK\r\n' + headers +
                    b'Connection: close\r\n\r\n' + body
                )
            finally:
                client.close()
                listener.close()

        self._raw_ports = getattr(self, '_raw_ports', {})
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(('127.0.0.1', 0))
        listener.listen(1)
        # Publish the port only after the socket is bound, avoiding a race
        # with the test thread reading it.
        self._raw_ports[path] = listener.getsockname()[1]
        thread = threading.Thread(target=handle, args=(listener,))
        thread.daemon = True
        thread.start()
        self._raw_sockets.append(thread)

    def raw_url(self, path):
        return 'http://127.0.0.1:%s%s' % (self._raw_ports[path], path)

    def stop(self):
        self.shutdown()
        self.server_close()
        self._thread.join(timeout=5)
