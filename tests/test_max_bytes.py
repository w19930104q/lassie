# -*- coding: utf-8 -*-

"""Tests for the ``max_bytes`` fetch budget against a real local HTTP server.

These tests intentionally do not mock lassie's retrieval layer: the server
speaks real HTTP (including gzip, chunked transfer encoding and redirects)
so the assertions cover what lassie actually reads from sockets.
"""

import json
import unittest

import lassie
from lassie import Lassie, LassieError, LassieMaxBytesExceeded
from lassie.filters.oembed.providers import consumer

from .http_server import LocalHTTPServer

#: oEmbed endpoint registered against the test server (see setUp/tearDown).
TEST_OEMBED_SCHEME = 'http://127.0.0.1:*/*'


def _html(body_text):
    return (
        '<!DOCTYPE html><html><head><title>%s</title></head>'
        '<body><p>%s</p></body></html>'
    ) % (body_text, body_text)


class MaxBytesTestCase(unittest.TestCase):
    def setUp(self):
        self.server = LocalHTTPServer()
        import oembed
        self._endpoint = oembed.OEmbedEndpoint(
            self.server.url('/oembed'), [TEST_OEMBED_SCHEME]
        )
        consumer.addEndpoint(self._endpoint)

    def tearDown(self):
        consumer.delEndpoint(self._endpoint)
        self.server.stop()

    # ---- basic behavior -------------------------------------------------

    def test_default_is_unlimited(self):
        body = _html('x' * 100000).encode('utf-8')
        self.server.set_route('/big', body=body, headers={
            'Content-Type': 'text/html; charset=utf-8'
        })

        l = Lassie()
        self.assertIsNone(l.max_bytes)

        data = l.fetch(self.server.url('/big'))
        self.assertEqual(data['status_code'], 200)
        self.assertEqual(len(data['html'].encode('utf-8')), len(body))

    def test_equals_limit_succeeds(self):
        body = _html('exact').encode('utf-8')
        self.server.set_route('/exact', body=body, headers={
            'Content-Type': 'text/html; charset=utf-8'
        })

        data = lassie.fetch(self.server.url('/exact'), max_bytes=len(body))
        self.assertEqual(data['status_code'], 200)
        self.assertIn('exact', data['html'])

    def test_one_byte_over_limit_fails(self):
        body = _html('overflow').encode('utf-8')
        self.server.set_route('/overflow', body=body, headers={
            'Content-Type': 'text/html; charset=utf-8'
        })

        with self.assertRaises(LassieMaxBytesExceeded) as ctx:
            lassie.fetch(self.server.url('/overflow'), max_bytes=len(body) - 1)

        error = ctx.exception
        self.assertIsInstance(error, LassieError)
        self.assertEqual(error.max_bytes, len(body) - 1)
        self.assertLessEqual(error.read_bytes, len(body) - 1)
        self.assertEqual(error.url, self.server.url('/overflow'))

    def test_limit_zero_on_nonempty_body_fails(self):
        body = _html('a').encode('utf-8')
        self.server.set_route('/zero', body=body, headers={
            'Content-Type': 'text/html; charset=utf-8'
        })

        with self.assertRaises(LassieMaxBytesExceeded):
            lassie.fetch(self.server.url('/zero'), max_bytes=0)

    def test_limit_zero_on_empty_body_succeeds(self):
        # HEAD-equivalent style response: no body bytes at all.
        self.server.set_route('/empty', body=b'', headers={
            'Content-Type': 'text/html; charset=utf-8'
        })

        with self.assertRaises(LassieError):
            # No content to parse, but the budget itself must not trip.
            lassie.fetch(self.server.url('/empty'), max_bytes=0)

    def test_instance_level_max_bytes(self):
        body = _html('instance').encode('utf-8')
        self.server.set_route('/instance', body=body, headers={
            'Content-Type': 'text/html; charset=utf-8'
        })

        l = Lassie()
        l.max_bytes = 5
        with self.assertRaises(LassieMaxBytesExceeded):
            l.fetch(self.server.url('/instance'))

    def test_fetch_argument_overrides_instance(self):
        body = _html('override').encode('utf-8')
        self.server.set_route('/override', body=body, headers={
            'Content-Type': 'text/html; charset=utf-8'
        })

        l = Lassie()
        l.max_bytes = 1  # would fail
        # A generous fetch-level budget takes precedence over the class one.
        data = l.fetch(self.server.url('/override'), max_bytes=len(body))
        self.assertEqual(data['status_code'], 200)

        # And fetch-level None falls back to the instance setting.
        with self.assertRaises(LassieMaxBytesExceeded):
            l.fetch(self.server.url('/override'), max_bytes=None)

    def test_invalid_max_bytes(self):
        self.server.set_route('/ok', body=_html('ok').encode('utf-8'),
                              headers={'Content-Type': 'text/html; charset=utf-8'})

        l = Lassie()
        for bad in ('100', 1.5, -1):
            with self.assertRaises((TypeError, ValueError)):
                l.fetch(self.server.url('/ok'), max_bytes=bad)

        # Booleans are ints in Python; reject them explicitly.
        with self.assertRaises(TypeError):
            l.fetch(self.server.url('/ok'), max_bytes=True)

    # ---- transport edge cases ------------------------------------------

    def test_gzip_counts_decompressed_bytes(self):
        plain = _html('gzip-' + ('z' * 2000)).encode('utf-8')
        self.server.set_route('/gzip', body=plain, gzip=True, headers={
            'Content-Type': 'text/html; charset=utf-8'
        })

        # A budget between the compressed and the decompressed size must
        # fail, proving decompressed bytes are counted.
        import gzip as _gzip
        compressed = _gzip.compress(plain)
        self.assertLess(len(compressed), len(plain))

        with self.assertRaises(LassieMaxBytesExceeded) as ctx:
            lassie.fetch(self.server.url('/gzip'), max_bytes=len(compressed) + 10)
        self.assertEqual(ctx.exception.url, self.server.url('/gzip'))

        # The exact decompressed length still succeeds.
        data = lassie.fetch(self.server.url('/gzip'), max_bytes=len(plain))
        self.assertEqual(data['status_code'], 200)

    def test_chunked_transfer_encoding(self):
        plain = _html('chunked-' + ('c' * 5000)).encode('utf-8')
        self.server.set_route(
            '/chunked', body=plain, chunked=True,
            headers={'Content-Type': 'text/html; charset=utf-8'}
        )

        # No Content-Length is sent; urllib3 reassembles the chunked stream
        # and lassie must account every decompressed byte of it.
        with self.assertRaises(LassieMaxBytesExceeded):
            lassie.fetch(self.server.url('/chunked'), max_bytes=100)

        data = lassie.fetch(self.server.url('/chunked'))
        self.assertEqual(len(data['html'].encode('utf-8')), len(plain))

    def test_missing_content_length(self):
        plain = _html('no-cl-' + ('n' * 4000)).encode('utf-8')
        # Chunked framing, still without any Content-Length header.
        self.server.set_route(
            '/nocl', body=plain, chunked=True,
            headers={'Content-Type': 'text/html; charset=utf-8'}
        )

        with self.assertRaises(LassieMaxBytesExceeded):
            lassie.fetch(self.server.url('/nocl'), max_bytes=50)

    def test_untrustworthy_content_length(self):
        plain = _html('lying-cl-' + ('l' * 4000)).encode('utf-8')
        # The server advertises an inflated Content-Length but closes the
        # connection after sending the real (much shorter) body. The bytes
        # lassie streams before EOF must still be counted against the budget
        # instead of trusting the advertised 9,999,999 bytes up front.
        self.server.serve_raw_on_close(
            '/lying',
            b'Content-Type: text/html; charset=utf-8\r\n'
            b'Content-Length: 9999999\r\n',
            plain,
        )

        l = Lassie()
        # Budget smaller than the real body: the limit is hit on actual reads
        # before the premature EOF can surface as a connection error.
        with self.assertRaises(LassieMaxBytesExceeded) as ctx:
            l.fetch(self.server.raw_url('/lying'), max_bytes=50)
        self.assertLessEqual(ctx.exception.read_bytes, 50)

        # The aborted response is closed, so the same client still works.
        good = _html('good').encode('utf-8')
        self.server.set_route('/good', body=good, headers={
            'Content-Type': 'text/html; charset=utf-8'
        })
        data = l.fetch(self.server.url('/good'))
        self.assertEqual(data['status_code'], 200)

    # ---- redirect accumulation ------------------------------------------

    def test_redirect_bodies_count_toward_budget(self):
        redirect_body = b'redirect-payload-' + (b'r' * 300)
        final_body = _html('final').encode('utf-8')
        self.server.set_route('/start', redirect='/middle',
                              body=redirect_body)
        self.server.set_route('/middle', redirect='/landing',
                              body=redirect_body)
        self.server.set_route('/landing', body=final_body, headers={
            'Content-Type': 'text/html; charset=utf-8'
        })

        total = len(redirect_body) * 2 + len(final_body)

        # Exactly the cumulative total succeeds.
        data = lassie.fetch(self.server.url('/start'), max_bytes=total)
        self.assertEqual(data['status_code'], 200)
        self.assertEqual(self.server.requests.count('/start'), 1)
        self.assertEqual(self.server.requests.count('/middle'), 1)
        self.assertEqual(self.server.requests.count('/landing'), 1)

        # A budget that fits the final page but not the redirect chain fails.
        with self.assertRaises(LassieMaxBytesExceeded) as ctx:
            lassie.fetch(self.server.url('/start'), max_bytes=len(final_body))
        self.assertIn('/start', ctx.exception.url)

    def test_budget_failure_during_redirect_chain(self):
        big_redirect = b'x' * 5000
        self.server.set_route('/r1', redirect='/r2', body=big_redirect)
        self.server.set_route('/r2', body=_html('never').encode('utf-8'),
                              headers={'Content-Type': 'text/html; charset=utf-8'})

        with self.assertRaises(LassieMaxBytesExceeded):
            lassie.fetch(self.server.url('/r1'), max_bytes=100)
        # The second request must never have been issued.
        self.assertNotIn('/r2', self.server.requests)

    def test_too_many_redirects_matches_client_max(self):
        self.server.set_route('/loop/0', redirect='/loop/1', body=b'0')
        self.server.set_route('/loop/1', redirect='/loop/2', body=b'1')
        self.server.set_route('/loop/2', redirect='/loop/3', body=b'2')
        self.server.set_route('/loop/3', body=_html('end').encode('utf-8'),
                              headers={'Content-Type': 'text/html; charset=utf-8'})

        l = Lassie()
        l.client.max_redirects = 2
        # Three hops are needed; the client allows two, so the third is
        # refused with requests' TooManyRedirects wrapped in LassieError.
        with self.assertRaises(LassieError):
            l.fetch(self.server.url('/loop/0'))

    def test_allow_redirects_false_is_respected(self):
        self.server.set_route('/hold', redirect='/destination', body=b'stay')
        self.server.set_route('/destination', body=_html('end').encode('utf-8'),
                              headers={'Content-Type': 'text/html; charset=utf-8'})

        l = Lassie()
        l.request_opts = {'allow_redirects': False}
        data = l.fetch(self.server.url('/hold'))
        # The redirect response is returned without following it.
        self.assertEqual(data['status_code'], 302)
        self.assertNotIn('/destination', self.server.requests)
        self.assertIn(b'stay'.decode(), data['html'])

    # ---- oEmbed interactions --------------------------------------------

    def test_oembed_bytes_accumulate_with_html(self):
        oembed_payload = json.dumps({
            'type': 'rich',
            'version': '1.0',
            'title': 'embedded',
            'html': '<iframe></iframe>',
            'width': 640,
            'height': 480,
            'provider_name': 'Local',
        }).encode('utf-8')
        html_body = _html('page').encode('utf-8')
        self.server.set_route('/oembed', body=oembed_payload, headers={
            'Content-Type': 'application/json'
        })
        self.server.set_route('/video/page', body=html_body, headers={
            'Content-Type': 'text/html; charset=utf-8'
        })

        total = len(oembed_payload) + len(html_body)
        data = lassie.fetch(self.server.url('/video/page'), max_bytes=total)
        self.assertEqual(data['oembed']['title'], 'embedded')

        # The oEmbed endpoint's own request headers (python-oembed's default
        # User-Agent) must be merged into lassie's client headers.
        oembed_index = self.server.requests.index('/oembed')
        self.assertEqual(
            self.server.request_headers[oembed_index]['User-Agent'],
            'python-oembed'
        )

        # A budget consumed entirely by the oEmbed response must fail on the
        # subsequent HTML read rather than silently returning oEmbed alone.
        with self.assertRaises(LassieMaxBytesExceeded):
            lassie.fetch(
                self.server.url('/video/page'), max_bytes=len(oembed_payload)
            )

    def test_oembed_budget_failure_not_swallowed_by_fallback(self):
        big_oembed = b'{' + (b'"padding": "' + b'p' * 8000) + b'"}'
        self.server.set_route('/oembed', body=big_oembed, headers={
            'Content-Type': 'application/json'
        })
        self.server.set_route('/video/big', body=_html('page').encode('utf-8'),
                              headers={'Content-Type': 'text/html; charset=utf-8'})

        # The generic oEmbed fallback (except LassieError) must not catch
        # LassieMaxBytesExceeded: it propagates and no truncated data is
        # returned.
        with self.assertRaises(LassieMaxBytesExceeded):
            lassie.fetch(self.server.url('/video/big'), max_bytes=100)
        self.assertNotIn('/video/big', self.server.requests)

    def test_oembed_failure_still_falls_back_to_html(self):
        # A 404-style oEmbed miss keeps the historical fallback behavior.
        self.server.set_route('/video/miss', body=_html('fallback').encode('utf-8'),
                              headers={'Content-Type': 'text/html; charset=utf-8'})

        data = lassie.fetch(self.server.url('/video/miss'))
        self.assertEqual(data['status_code'], 200)
        self.assertIsNone(data.get('oembed'))

    def test_oembed_unsupported_mime_type_falls_back(self):
        # Endpoint matches but the response is not JSON/XML: python-oembed's
        # mime-type validation fails and lassie falls back to the HTML page.
        self.server.set_route('/oembed', body=b'not json', headers={
            'Content-Type': 'text/plain'
        })
        self.server.set_route('/video/plain', body=_html('plain').encode('utf-8'),
                              headers={'Content-Type': 'text/html; charset=utf-8'})

        data = lassie.fetch(self.server.url('/video/plain'))
        self.assertEqual(data['status_code'], 200)
        self.assertIsNone(data.get('oembed'))

    def test_no_oembed_endpoint_for_url(self):
        # With the test endpoint removed, the URL matches no provider; the
        # oEmbed step must degrade gracefully instead of failing the fetch.
        consumer.delEndpoint(self._endpoint)
        try:
            self.server.set_route('/plain/page', body=_html('plain').encode('utf-8'),
                                  headers={'Content-Type': 'text/html; charset=utf-8'})
            data = lassie.fetch(self.server.url('/plain/page'))
            self.assertEqual(data['status_code'], 200)
            self.assertIsNone(data.get('oembed'))
        finally:
            consumer.addEndpoint(self._endpoint)

    def test_head_file_content_does_not_consume_budget(self):
        # HEAD requests have no body, so a zero budget must not interfere
        # with handle_file_content's content-type probe.
        self.server.set_route('/photo.jpg', body=b'\xff\xd8\xff\xe0' + b'j' * 2000,
                              headers={'Content-Type': 'image/jpeg'})

        l = Lassie()
        data = l.fetch(
            self.server.url('/photo.jpg'),
            handle_file_content=True, max_bytes=0
        )
        self.assertEqual(data['images'][0]['type'], 'body_image')
        self.assertEqual(data['images'][0]['src'], self.server.url('/photo.jpg'))

    # ---- client reuse after failure -------------------------------------

    def test_client_reusable_after_budget_failure(self):
        body = _html('reuse').encode('utf-8')
        self.server.set_route('/fail', body=body, headers={
            'Content-Type': 'text/html; charset=utf-8'
        })
        self.server.set_route('/ok', body=body, headers={
            'Content-Type': 'text/html; charset=utf-8'
        })

        l = Lassie()
        with self.assertRaises(LassieMaxBytesExceeded):
            l.fetch(self.server.url('/fail'), max_bytes=5)

        # The same Lassie instance (and its requests Session/pool) must
        # complete the next fetch without leaking the aborted connection.
        data = l.fetch(self.server.url('/ok'))
        self.assertEqual(data['status_code'], 200)
        self.assertIn('reuse', data['html'])

        # Repeated failure -> success cycles to shake out connection leaks.
        for _ in range(3):
            with self.assertRaises(LassieMaxBytesExceeded):
                l.fetch(self.server.url('/fail'), max_bytes=5)
            data = l.fetch(self.server.url('/ok'))
            self.assertEqual(data['status_code'], 200)

    def test_response_format_unchanged_on_success(self):
        body = (
            '<!DOCTYPE html><html lang="en"><head>'
            '<meta property="og:title" content="OG Title">'
            '<title>Page Title</title></head>'
            '<body><h1>Hi</h1></body></html>'
        ).encode('utf-8')
        self.server.set_route('/meta', body=body, headers={
            'Content-Type': 'text/html; charset=utf-8'
        })

        data = lassie.fetch(self.server.url('/meta'))
        self.assertEqual(data['title'], 'OG Title')
        self.assertEqual(data['url'], self.server.url('/meta'))
        self.assertEqual(data['status_code'], 200)
        self.assertEqual(data['videos'], [])
        self.assertIsInstance(data['images'], list)
        self.assertEqual(data['locale'], 'en_US')
        self.assertEqual(data['html'], body.decode('utf-8'))


if __name__ == '__main__':
    unittest.main()
