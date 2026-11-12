.. _advanced-usage:

Advanced Usage
==============

This section will cover how to use the ``Lassie`` class to maintain settings across all ``fetch`` calls.


Class Level Attributes
----------------------

Constructing a ``Lassie`` class and calling ``fetch`` will use all the default params that are available to ``fetch``.

.. code-block:: python

    >>> from lassie import Lassie
    >>> l = Lassie()

    >>> l.fetch('https://github.com/michaelhelmick')
    {
        'images': [{
            'src': u'https://github.global.ssl.fastly.net/images/modules/logos_page/Octocat.png',
            'type': u'og:image'
        }, {
            'src': u'https://github.com/favicon.ico',
            'type': u'favicon'
        }],
        'url': 'https://github.com/michaelhelmick',
        'description': u'michaelhelmick has 22 repositories written in Python, Shell, and JavaScript. Follow their code on GitHub.',
        'videos': [],
        'title': u'michaelhelmick (Mike Helmick) \xb7 GitHub'
    }
    >>> l.fetch('https://github.com/ashibble')
    {
        'images': [{
            'src': u'https://github.global.ssl.fastly.net/images/modules/logos_page/Octocat.png',
            'type': u'og:image'
        }, {
            'src': u'https://github.com/favicon.ico',
            'type': u'favicon'
        }],
        'url': 'https://github.com/ashibble',
        'description': u'Follow ashibble on GitHub and watch them build beautiful projects.',
        'videos': [],
        'title': u'ashibble (Alexander Shibble) \xb7 GitHub'
    }

If you decide that you don't want to filter for Open Graph data, instead of declaring ``open_graph=False`` in every ``fetch`` call:

.. code-block:: python

    >>> import lassie
    >>> l = Lassie()
    >>> l.fetch('https://github.com/michaelhelmick', open_graph=False)
    >>> l.fetch('https://github.com/ashibble', open_graph=False)

You can use the ``Lassie`` class and set attibutes on the class.

.. code-block:: python

    >>> from lassie import Lassie
    >>> l = Lassie()
    >>> l.open_graph = False

    >>> l.fetch('https://github.com/michaelhelmick')
    {
        'images': [{
            'src': u'https://github.com/favicon.ico',
            'type': u'favicon'
        }],
        'url': 'https://github.com/michaelhelmick',
        'description': u'michaelhelmick has 22 repositories written in Python, Shell, and JavaScript. Follow their code on GitHub.',
        'videos': [],
        'title': u'michaelhelmick (Mike Helmick) \xb7 GitHub'
    }
    >>> l.fetch('https://github.com/ashibble')
    {
        'images': [{
            'src': u'https://github.com/favicon.ico',
            'type': u'favicon'
        }],
        'url': 'https://github.com/ashibble',
        'description': u'Follow ashibble on GitHub and watch them build beautiful projects.',
        'videos': [],
        'title': u'ashibble (Alexander Shibble) \xb7 GitHub'
    }

You'll notice the data for the Open Graph properties wasn't returned in the last responses. That's because passing ``open_graph=False`` tells Lassie to not filter for those properties.

In the edge case that there is a time or two you want to override the class attribute, just pass the parameter to ``fetch`` and Lassie will use that parameter.

.. code-block:: python

    >>> from lassie import Lassie
    >>> l = Lassie()
    >>> l.open_graph = False

    >>> l.fetch('https://github.com/michaelhelmick')
    {
        'images': [{
            'src': u'https://github.com/favicon.ico',
            'type': u'favicon'
        }],
        'url': 'https://github.com/michaelhelmick',
        'description': u'michaelhelmick has 22 repositories written in Python, Shell, and JavaScript. Follow their code on GitHub.',
        'videos': [],
        'title': u'michaelhelmick (Mike Helmick) \xb7 GitHub'
    }
    >>> l.fetch('https://github.com/ashibble', open_graph=True)
    {
        'images': [{
            'src': u'https://github.global.ssl.fastly.net/images/modules/logos_page/Octocat.png',
            'type': u'og:image'
        }, {
            'src': u'https://github.com/favicon.ico',
            'type': u'favicon'
        }],
        'url': 'https://github.com/ashibble',
        'description': u'Follow ashibble on GitHub and watch them build beautiful projects.',
        'videos': [],
        'title': u'ashibble (Alexander Shibble) \xb7 GitHub'
    }


Manipulate the Request (headers, proxies, etc.)
-----------------------------------------------

There are times when you may want to turn SSL verification off, send custom headers, or add proxies for the request to go through.

Lassie uses the `requests <http://python-requests.org>`_ library to make web requests. ``requests`` accepts a few parameters to allow developers to manipulate the acutal HTTP request.

Here is an example of sending custom headers to a lassie request:

.. code-block:: python

    from lassie import Lassie

    l = Lassie()
    l.request_opts = {
        'headers': {
            'User-Agent': 'python lassie'
        }
    }
    l.fetch('http://google.com')

Maybe you want to set a request timeout, here's another example:

.. code-block:: python

    from lassie import Lassie

    l = Lassie()
    l.request_opts = {
        'timeout': 10  # 10 seconds
    }

    # If the response takes longer than 10 seconds this request will fail
    l.fetch('http://google.com')


Limiting Response Size (``max_bytes``)
--------------------------------------

A page (or an oEmbed response it triggers) can be arbitrarily large. Pass
``max_bytes`` to cap how much data a single ``fetch`` is allowed to read.
The same keyword is available both on the module-level ``lassie.fetch`` and
on a ``Lassie`` instance, where it can be set once and reused:

.. code-block:: python

    import lassie
    from lassie import Lassie, LassieMaxBytesExceeded

    # Per-call limit (the top-level fetch accepts the same keyword).
    try:
        lassie.fetch('http://example.com/huge-page', max_bytes=5 * 1024 * 1024)
    except LassieMaxBytesExceeded as e:
        print(e)  # 'Response body exceeded the max_bytes budget of 5242880 ...'

    # Instance-level default for every fetch on this client.
    l = Lassie()
    l.max_bytes = 1024 * 1024
    l.fetch('http://example.com/page')

    # A per-call value takes precedence; pass nothing to stay unlimited.
    l.fetch('http://example.com/page', max_bytes=1024)

How the budget works:

* The default is ``None``, meaning no limit, which preserves the previous
  behavior exactly.
* The budget counts **decompressed body bytes** -- the raw bytes before they
  are decoded into characters. gzip-compressed responses therefore count at
  their expanded size, and chunked transfer encoding is reassembled before
  counting.
* One budget is shared by everything a fetch reads: the oEmbed response, the
  HTML response and every intermediate redirect response. ``HEAD`` requests
  have no body and do not count.
* Reading *exactly* ``max_bytes`` bytes succeeds. The first chunk that would
  push the total past the limit stops the read immediately -- the full body
  is never buffered -- and raises
  :class:`~lassie.exceptions.LassieMaxBytesExceeded`, a subclass of
  :class:`~lassie.exceptions.LassieError`. No truncated result is returned.
* ``Content-Length`` is never trusted: missing, inflated or otherwise
  unreliable headers make no difference because only bytes actually read
  from the socket are counted.
* The exception is not swallowed by the oEmbed fallback logic: if the
  oEmbed response itself exceeds the budget, the error propagates instead
  of silently continuing with partial data. The partially-read response is
  closed first, so a reused client keeps working on the next fetch.

The exception exposes the configured limit, the bytes already read and the
offending URL:

.. code-block:: python

    try:
        lassie.fetch('http://example.com/huge-page', max_bytes=100)
    except LassieMaxBytesExceeded as e:
        e.max_bytes   # 100
        e.read_bytes  # bytes accounted for before the limit was crossed
        e.url         # URL whose body crossed the budget


Playing Nice with non-HTML Files
--------------------------------

Sometimes, you may want to grab information about an image or other type of file. Although only images are supported, you can retrieve a nicely structured ``dict``

Pass ``handle_file_content=True`` to ``lassie.fetch`` or set it on a ``Lassie`` instance

.. code-block:: python

    >>> from lassie import Lassie

    >>> lassie.fetch('https://camo.githubusercontent.com/d19b279de191489445d8cfd39faf93e19ca2df14/68747470733a2f2f692e696d6775722e636f6d2f5172764e6641582e676966', handle_file_content=True)
    {
        'title': '68747470733a2f2f692e696d6775722e636f6d2f5172764e6641582e676966',
        'videos': [],
        'url': 'https://camo.githubusercontent.com/d19b279de191489445d8cfd39faf93e19ca2df14/68747470733a2f2f692e696d6775722e636f6d2f5172764e6641582e676966',
        'images': [{
            'type': 'body_image',
            'src': 'https://camo.githubusercontent.com/d19b279de191489445d8cfd39faf93e19ca2df14/68747470733a2f2f692e696d6775722e636f6d2f5172764e6641582e676966'
        }]
    }

    >>> lassie.fetch('http://2.bp.blogspot.com/-vzGgFFtW-VY/Tz-eozaHw3I/AAAAAAAAM3k/OMvxpFYr23s/s1600/The-best-top-desktop-cat-wallpapers-10.jpg', handle_file_content=True)
    {
        'title': 'The-best-top-desktop-cat-wallpapers-10.jpg',
        'images': [{
            'type': 'body_image',
            'src': 'http://2.bp.blogspot.com/-vzGgFFtW-VY/Tz-eozaHw3I/AAAAAAAAM3k/OMvxpFYr23s/s1600/The-best-top-desktop-cat-wallpapers-10.jpg'
        }],
        'videos': [],
        'url': 'http://2.bp.blogspot.com/-vzGgFFtW-VY/Tz-eozaHw3I/AAAAAAAAM3k/OMvxpFYr23s/s1600/The-best-top-desktop-cat-wallpapers-10.jpg'
    }
