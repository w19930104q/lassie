# -*- coding: utf-8 -*-

"""
lassie.core
~~~~~~~~~~~

This module contains a Lassie object to maintain settings across lassie.

"""


import json
import re
from os.path import basename

import requests
from bs4 import BeautifulSoup
from requests import Request, Session

from .compat import str, urljoin, urlparse
from .exceptions import LassieError, LassieMaxBytesExceeded
from .filters import FILTER_MAPS
from .filters.oembed.providers import (
    build_oembed_url, get_oembed_endpoint, parse_oembed_data, parse_oembed_response,
)
from .utils import (
    FAKE_USER_AGENT, READ_CHUNK_SIZE, ByteBudget, clean_text, convert_to_int,
    determine_user_agent, normalize_image_data, normalize_locale,
)

REQUEST_OPTS = {
    'client': ('cert', 'headers', 'hooks', 'max_redirects', 'proxies'),
    'request': ('timeout', 'allow_redirects', 'stream', 'verify'),
}

IMAGE_MIMETYPES = [
    'image/jpeg', 'image/gif', 'image/bmp', 'image/png'
]


def merge_settings(fetch_setting, class_setting):
    """Merge settings for ``fetch``, method params have priority."""
    if fetch_setting is None:
        return class_setting
    else:
        return fetch_setting


class Lassie(object):
    __attrs__ = [
        'open_graph', 'twitter_card', 'touch_icon', 'favicon',
        'canonical', 'all_images', 'parser', '_retrieve_content',
        'client', 'max_bytes'
    ]

    def __init__(self):
        """Instantiates an instance of Lassie."""
        self.open_graph = True
        self.twitter_card = True
        self.touch_icon = True
        self.favicon = True
        self.canonical = False
        self.all_images = False
        self.parser = 'html5lib'
        self.handle_file_content = False
        self.user_agent_set_manually = False
        self._request_opts = {}
        #: Optional per-fetch cap on decompressed body bytes. ``None`` means
        #: no limit; a per-call ``max_bytes`` argument takes precedence.
        self.max_bytes = None
        self.client = Session()

    @property
    def request_opts(self):
        return self._request_opts

    @request_opts.setter
    def request_opts(self, _dict):
        for k, v in _dict.items():
            if (k in REQUEST_OPTS['client'] or k in REQUEST_OPTS['request']):
                self._request_opts[k] = v

            if k in REQUEST_OPTS['client']:
                setattr(self.client, k, v)

        if not self.client.headers or not isinstance(self.client.headers, (dict, requests.structures.CaseInsensitiveDict)):
            self.client.headers = {}

        self.client.headers = requests.structures.CaseInsensitiveDict(self.client.headers)

        user_agent = self.client.headers.get('User-Agent')
        self.client.headers['User-Agent'] = determine_user_agent(user_agent)

        if user_agent != requests.utils.default_user_agent() and user_agent != FAKE_USER_AGENT:
            self.user_agent_set_manually = True
        else:
            self.user_agent_set_manually = False

    def __repr__(self):
        return '<Lassie [parser: %s]>' % (self.parser)

    def fetch(self, url, open_graph=None, twitter_card=None, touch_icon=None,
              favicon=None, all_images=None, parser=None, handle_file_content=None,
              canonical=None, max_bytes=None):
        """Retrieves content from the specified url, parses it, and returns
        a beautifully crafted dictionary of important information about that
        web page.

        Priority tree is as follows:
            1. OEmbed
            2. Open Graph
            3. Twitter Card
            4. Other meta content (i.e. description, keywords)

        :param url: URL to send a GET request to
        :param open_graph: (optional) If ``True``, filters web page content for Open Graph meta tags. The content of these properties have top priority on return values.
        :type open_graph: bool
        :param twitter_card: (optional) If ``True``, filters web page content for Twitter Card meta tags
        :type twitter_card: bool
        :param touch_icon: (optional) If ``True``, retrieves Apple touch icons and includes them in the response ``images`` array
        :type touch_icon: bool
        :param favicon: (optional) If ``True``, retrieves any favicon images and includes them in the response ``images`` array
        :type favicon: bool
        :param canonical: (optional) If ``True``, retrieves canonical url from meta tags. Default: False
        :type canonical: bool
        :param all_images: (optional) If ``True``, retrieves images inside web pages body and includes them in the response ``images`` array. Default: False
        :type all_images: bool
        :param parser: (optional) String reference for the parser that BeautifulSoup will use
        :type parser: string
        :param handle_file_content: (optional) If ``True``, lassie will return a generic response when a file is fetched. Default: False
        :type handle_file_content: bool
        :param max_bytes: (optional) Maximum number of decompressed body bytes this fetch may read in total across the HTML response, the oEmbed response and any intermediate redirect responses. Bodies are streamed, so the read stops as soon as the limit would be exceeded and a :class:`~lassie.exceptions.LassieMaxBytesExceeded` is raised; exactly ``max_bytes`` bytes is still successful. Defaults to the instance's ``max_bytes`` attribute, which itself defaults to ``None`` (unlimited).
        :type max_bytes: int or None

        """

        # Set params, method params have priority over class params
        open_graph = merge_settings(open_graph, self.open_graph)
        twitter_card = merge_settings(twitter_card, self.twitter_card)
        touch_icon = merge_settings(touch_icon, self.touch_icon)
        favicon = merge_settings(favicon, self.favicon)
        canonical = merge_settings(canonical, self.canonical)
        all_images = merge_settings(all_images, self.all_images)
        parser = merge_settings(parser, self.parser)
        handle_file_content = merge_settings(handle_file_content, self.handle_file_content)
        max_bytes = merge_settings(max_bytes, self.max_bytes)

        if max_bytes is not None:
            if isinstance(max_bytes, bool) or not isinstance(max_bytes, int):
                raise TypeError('max_bytes must be a positive integer or None')
            if max_bytes < 0:
                raise ValueError('max_bytes must be a positive integer or None')

        # One budget for every response read during this fetch, so the limit
        # accumulates across oEmbed, HTML and intermediate redirect responses.
        byte_budget = ByteBudget(max_bytes)

        data = {
            'images': [],
            'videos': [],
        }

        has_file_content = False
        content_type = None
        if handle_file_content:
            headers, status_code = self._retrieve_headers(url)
            content_type = headers.get('Content-Type')
            has_file_content = content_type and not 'text/html' in content_type

        if has_file_content and content_type:
            has_image_content = content_type in IMAGE_MIMETYPES
            if has_image_content:
                parsed_url = urlparse(url)
                data['title'] = basename(parsed_url.path.lstrip('/'))  # TODO: if the url doesn't have an extension, maybe we should match it up to the mimetype and append an ext?
                data['url'] = url
                data['images'].append({
                    'type': 'body_image',
                    'src': url,
                })
        else:
            # LassieMaxBytesExceeded is a LassieError on purpose for callers
            # that catch the base class, but it must not be swallowed by the
            # oEmbed fallback below, so it is handled explicitly.
            try:
                oembed_data, status_code = self._retrieve_oembed_data(url, byte_budget)
                parse_oembed_data(oembed_data, data)
            except LassieMaxBytesExceeded:
                raise
            except LassieError:
                oembed_data = None

            html, status_code = self._retrieve_content(url, byte_budget)

            if not html and not oembed_data:
                raise LassieError('There was no content to parse.')

            if '<html' not in html:
                html = re.sub(r'(?:<!DOCTYPE(?:\s\w)?>(?:<head>)?)', '<!DOCTYPE html><html>', html)

            soup = BeautifulSoup(clean_text(html), parser)

            self._filter_amp_data(soup, data, url, all_images)

            if open_graph:
                self._filter_meta_data('open_graph', soup, data, url)

            if twitter_card:
                self._filter_meta_data('twitter_card', soup, data)

            self._filter_meta_data('generic', soup, data)

            if touch_icon:
                self._filter_link_tag_data('touch_icon', soup, data, url)

            if favicon:
                self._filter_link_tag_data('favicon', soup, data, url)

            if canonical:
                self._filter_link_tag_data('canonical', soup, data, url)

            if all_images:
                # Maybe filter out 1x1, no "good" way to do this if image doesn't supply
                # width/height.
                self._find_all_images(soup, data, url)

            # TODO: Find a good place for setting url, title and locale
            if soup.html.get('lang'):
                lang = soup.html.get('lang')
            else:
                lang = soup.html.get('xml:lang')

            if lang and ('locale' not in data):
                locale = normalize_locale(lang)
                if locale:
                    data['locale'] = locale

            data_url = data.get('url')
            if not data_url or (data_url in url and len(data_url) < len(url)):
                data['url'] = url

            if ('title' not in data or not data.get('title')) and hasattr(soup.title, 'string'):
                data['title'] = soup.title.string

            data['html'] = html

        data['status_code'] = status_code

        return data

    def _prepare_request(self, method, url, headers, **request_kwargs):
        request = Request(method, url, headers=headers)
        prepped = request.prepare()

        if not self.user_agent_set_manually:
            prepped.headers['User-Agent'] = determine_user_agent(prepped.headers.get('User-Agent'))

        return prepped

    def _retrieve_oembed_data(self, url, byte_budget=None):
        endpoint = get_oembed_endpoint(url)
        if endpoint is None:
            # Mirrors OEmbedConsumer.embed raising OEmbedNoEndpoint here.
            raise LassieError('There are no oEmbed endpoints available for %s' % url)

        oembed_url = build_oembed_url(endpoint, url)
        content, response, status_code = self._stream_get(
            oembed_url, byte_budget, headers=endpoint._requestHeaders
        )

        try:
            oembed_data = parse_oembed_response(
                content, response.headers.get('Content-Type')
            )
        except Exception as e:
            raise LassieError(e)

        return oembed_data, status_code

    def _retrieve_headers(self, url):
        request_kwargs = self.merge_request_kwargs()

        try:
            request = self._prepare_request(
                'HEAD', url, headers=self.client.headers, **request_kwargs
            )
            response = self.client.send(request, **request_kwargs)
        except requests.exceptions.RequestException as e:
            raise LassieError(e)

        return response.headers, response.status_code

    def _retrieve_content(self, url, byte_budget=None):
        _body, response, status_code = self._stream_get(url, byte_budget)
        # The streamed bytes were cached on the response, so .text applies
        # requests' normal charset detection (headers then HTML meta tags),
        # keeping decoding behavior identical to the previous implementation.
        return response.text, status_code

    def _stream_get(self, url, byte_budget=None, headers=None):
        """GET ``url`` while streaming every body through ``byte_budget``.

        Redirects are followed manually (each request is sent with
        ``allow_redirects=False``) so that every intermediate response body
        can be read in bounded chunks and accounted for instead of being
        consumed whole by requests. An explicit
        ``request_opts={'allow_redirects': False}`` is still honored: in that
        case the first redirect response is returned as-is. Bodies are read
        as decompressed bytes (gzip and chunked transfer encoding are handled
        by urllib3), which means missing or untrustworthy
        ``Content-Length`` headers never affect the accounting: only bytes
        actually pulled from the socket count.

        On success the final response's bytes are returned alongside the
        (closed) response and its status code. The response keeps its cached
        body, so ``.text`` and ``.headers`` remain usable after the connection
        has been released. On a budget failure the partially-read response
        (and any connection it holds) is closed before
        :class:`LassieMaxBytesExceeded` propagates, so the shared client stays
        usable for subsequent fetches.

        :returns: ``(body_bytes, response, status_code)``

        """
        if byte_budget is None:
            byte_budget = ByteBudget()

        request_kwargs = self.merge_request_kwargs()
        # Stream is required to enforce the budget during the read; callers
        # cannot turn it off. Redirect following is driven manually below so
        # redirect bodies count, while an explicit False is still respected.
        follow_redirects = request_kwargs.pop('allow_redirects', True)
        request_kwargs['stream'] = True
        request_kwargs['allow_redirects'] = False

        request_headers = self.client.headers
        if headers:
            request_headers = requests.structures.CaseInsensitiveDict(
                dict(self.client.headers, **headers)
            )

        response = None
        redirects_followed = 0
        try:
            while True:
                request = self._prepare_request(
                    'GET', url, headers=request_headers, **request_kwargs
                )
                response = self.client.send(request, **request_kwargs)

                body = self._read_body_with_budget(response, byte_budget)
                self._finalize_response(response, body)

                redirect_location = self.client.get_redirect_target(response)
                if redirect_location is None or not follow_redirects:
                    return body, response, response.status_code

                # This response's body has been fully read and closed; the
                # redirect target becomes the next request's url. Relative
                # locations and scheme-relative urls are resolved the same
                # way requests itself resolves them.
                url = urljoin(response.url, redirect_location)
                response = None

                # Mirror requests: before following, the current history
                # length is redirects_followed; refuse once that reaches the
                # configured maximum.
                if redirects_followed >= self.client.max_redirects:
                    raise requests.exceptions.TooManyRedirects(
                        'Exceeded %s redirects.' % self.client.max_redirects
                    )
                redirects_followed += 1
        except LassieMaxBytesExceeded as e:
            if e.url is None and response is not None:
                e.url = response.url
            raise
        except requests.exceptions.RequestException as e:
            raise LassieError(e)
        finally:
            # Budget failures and unexpected errors must never leave a
            # response (and its pooled connection) open.
            if response is not None:
                response.close()

    def _read_body_with_budget(self, response, byte_budget):
        """Stream ``response`` in chunks, accounting each chunk's size.

        Reading stops at the first chunk that would exceed the budget; the
        remainder of that chunk is discarded by closing the response.

        :raises LassieMaxBytesExceeded: when the cumulative read would exceed
            ``byte_budget.max_bytes``.

        """
        body = b''
        try:
            for chunk in response.iter_content(
                chunk_size=READ_CHUNK_SIZE, decode_unicode=False
            ):
                if not chunk:
                    continue
                # Raises as soon as this chunk would cross the limit; the
                # chunk itself is never appended, so no truncated body is
                # returned.
                byte_budget.account(chunk)
                body += chunk
        except requests.exceptions.RequestException as e:
            raise LassieError(e)

        return body

    def _finalize_response(self, response, body):
        """Populate the response's cached content and release its connection.

        Setting ``_content`` keeps ``response.text``/``response.encoding``
        semantics identical to a non-streamed request while ensuring the
        connection is returned to the pool even though the body was consumed
        through ``iter_content``.

        """
        response._content = body
        response._content_consumed = True
        response.close()

    def merge_request_kwargs(self):
        request_kwargs = {}

        for k, v in self._request_opts.items():
            if k in REQUEST_OPTS['request']:
                # Set request specific kwarg
                request_kwargs[k] = v

        return request_kwargs

    def _filter_meta_data(self, source, soup, data, url=None):
        """This method filters the web page content for meta tags that match patterns given in the ``FILTER_MAPS``

        :param source: The key of the meta dictionary in ``FILTER_MAPS['meta']``
        :type source: string
        :param soup: BeautifulSoup instance to find meta tags
        :type soup: instance
        :param data: The response dictionary to manipulate
        :type data: (dict)

        """
        meta = FILTER_MAPS['meta'][source]
        meta_map = meta['map']

        html = soup.find_all('meta', {meta['key']: meta['pattern']})

        image = {}
        video = {}

        for line in html:
            prop = line.get(meta['key'])
            value = line.get('content')
            _prop = meta_map.get(prop)

            if prop in meta_map and _prop and not data.get(_prop):
                # this could be bad in cases where any values that the property
                # is mapped up to (i.e. "src", "type", etc) are found in ``data``
                # TODO: Figure out a smoother way to prevent conflicts ^^^^^^^^
                image_prop = meta['image_key']
                video_prop = meta['video_key']

                if prop.startswith((image_prop, video_prop)) and \
                prop.endswith(('width', 'height')):
                    if prop.endswith(('width', 'height')):
                        value = convert_to_int(value)

                if meta_map[prop] == 'locale':
                    locale = normalize_locale(value)
                    if locale:
                        data['locale'] = locale

                if prop == 'keywords':
                    if isinstance(value, str):
                        value = [v.strip() for v in value.split(',')]
                    else:
                        value = []

                if image_prop and prop.startswith(image_prop) and value:
                    # og:image URLs can be relative
                    if prop == 'og:image' and url:
                        value = urljoin(url, value)
                    image[meta_map[prop]] = value
                elif video_prop and prop.startswith(video_prop) and value:
                    video[meta_map[prop]] = value
                else:
                    data[meta_map[prop]] = value

        if image:
            image['type'] = image_prop
            data['images'].append(image)
        if video:
            data['videos'].append(video)

    def _filter_link_tag_data(self, source, soup, data, url):
        """This method filters the web page content for link tags that match patterns given in the ``FILTER_MAPS``

        :param source: The key of the meta dictionary in ``FILTER_MAPS['link']``
        :type source: string
        :param soup: BeautifulSoup instance to find meta tags
        :type soup: instance
        :param data: The response dictionary to manipulate
        :type data: (dict)
        :param url: URL used for making an absolute url
        :type url: string

        """
        link = FILTER_MAPS['link'][source]

        html = soup.find_all('link', {link['key']: link['pattern']})

        if link['type'] == 'url':
            for line in html:
                data['url'] = line.get('href')
        else:
            for line in html:
                data['images'].append({
                    'src': urljoin(url, line.get('href')),
                    'type': link['type'],
                })

    def _filter_amp_data(self, soup, data, url, all_images):
        amp_scripts = soup.find_all('script', {'type': 'application/ld+json'})
        for script in amp_scripts:
            content = script.contents
            _json = None
            try:
                _json = json.loads(content[0])
            except (IndexError, ValueError):
                continue

            if _json:
                if isinstance(_json, list):
                    try:
                        # if the json is a list (see #46),
                        # set _json to the first item which _should_ be an object
                        _json = _json[0]
                    except IndexError:  # pragma: no cover
                        pass

                if isinstance(_json, object):
                    image = _json.get('image')
                    if image:
                        if isinstance(image, str):
                            data['images'].append({
                                'src': urljoin(url, image),
                            })
                        elif isinstance(image, list) or isinstance(image, object):
                            if isinstance(image, list):
                                image = image[0]

                            try:
                                image_list = image.get('@list')
                            except AttributeError:
                                image_list = [image]

                            if image_list:
                                for _image in image_list:
                                    if isinstance(_image, str):
                                        data['images'].append({
                                            'src': urljoin(url, _image),
                                        })
                                    elif isinstance(_image, object):
                                        data['images'].append({
                                            'src': urljoin(url, _image.get('url')),
                                            'width': convert_to_int(_image.get('width')),
                                            'height': convert_to_int(_image.get('height')),
                                        })
                            elif not image_list and image.get('url') and url != image.get('url'):
                                data['images'].append({
                                    'src': urljoin(url, image.get('url')),
                                    'width': convert_to_int(image.get('width')),
                                    'height': convert_to_int(image.get('height')),
                                })

                    thumbnail_url = _json.get('thumbnailUrl')
                    if thumbnail_url:
                        data['images'].append({
                            'src': urljoin(url, thumbnail_url),
                        })

                    _type = _json.get('@type')
                    if _type and _type == 'VideoObject':
                        video_src = _json.get('embedUrl')

                        if video_src:
                            data['videos'].append({
                                'src': video_src,
                                'width': convert_to_int(_json.get('width')),
                                'height': convert_to_int(_json.get('height')),
                            })

                        thumbnail = _json.get('thumbnail')
                        if thumbnail:
                            if isinstance(thumbnail, str):
                                data['images'].append({
                                    'src': urljoin(url, thumbnail),
                                })
                            elif isinstance(thumbnail, object):
                                if thumbnail.get('@list'):
                                    for _thumbnail in thumbnail.get('@list'):
                                        data['images'].append({
                                            'src': urljoin(url, _thumbnail.get('url')),
                                            'width': convert_to_int(_thumbnail.get('width')),
                                            'height': convert_to_int(_thumbnail.get('height')),
                                        })
                                else:
                                    data['images'].append({
                                        'src': urljoin(url, thumbnail.get('url')),
                                        'width': convert_to_int(thumbnail.get('width')),
                                        'height': convert_to_int(thumbnail.get('height')),
                                    })

                    data['title'] = _json.get('headline', '')
                    data['url'] = _json.get('url', '')
                    data['description'] = _json.get('description', '')

        if all_images:
            amp_imgs = soup.find_all('amp-img')
            for image in amp_imgs:
                item = normalize_image_data(image, url)

                data['images'].append(item)

    def _find_all_images(self, soup, data, url):
        """This method finds all images in the web page content

        :param soup: BeautifulSoup instance to find meta tags
        :type soup: instance
        :param data: The response dictionary to manipulate
        :type data: (dict)

        """
        all_images = soup.find_all('img')
        for image in all_images:
            item = normalize_image_data(image, url)

            data['images'].append(item)
