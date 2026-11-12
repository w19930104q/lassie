# -*- coding: utf-8 -*-

"""
lassie.exceptions
~~~~~~~~~~~~~~~~~

This module contains the set of Lassie exceptions.

"""

class LassieError(Exception):
    """Generic catch-all Exceptions"""
    pass


class LassieMaxBytesExceeded(LassieError):
    """Raised when a ``fetch`` would read more than its ``max_bytes`` budget.

    The budget counts the decompressed body bytes (bytes before they are
    decoded into characters) of every response read during a single
    ``fetch``: the HTML response, the oEmbed response and any intermediate
    redirect responses. Reading exactly ``max_bytes`` bytes succeeds; the
    first chunk that would push the running total past the limit stops the
    read immediately, closes the response and raises this exception.

    This is a subclass of :class:`LassieError`, but it is never swallowed by
    the oEmbed fallback logic, so no truncated result is ever returned.

    :param max_bytes: The configured byte budget.
    :param read_bytes: The number of bytes already read when the limit was
        hit (this does not include the unread remainder of the chunk that
        exceeded the budget).
    :param url: URL of the response whose body exceeded the budget, if known.

    """

    def __init__(self, max_bytes, read_bytes, url=None):
        self.max_bytes = max_bytes
        self.read_bytes = read_bytes
        self.url = url

        message = (
            'Response body exceeded the max_bytes budget of %d bytes '
            '(%d bytes read)' % (max_bytes, read_bytes)
        )
        if url:
            message = '%s while reading %s' % (message, url)

        super(LassieMaxBytesExceeded, self).__init__(message)
