# -*- coding: utf-8 -*-

"""
lassie.filters.providers
~~~~~~~~~~

This module contains oembed providers and a python oembed consumer.

"""

import re

import oembed
from oembed import OEmbedResponse

from ...utils import convert_to_int

HYPERLINK_PATTERN = re.compile(r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\(\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+')

PROVIDERS = {
    'http://www.youtube.com/oembed': [
        'https?://*.youtube.com/watch*',
        'https?://*.youtube.com/v/*',
        'https?://youtu.be/*',
        'https?://*.youtube.com/user/*',
        'https?://*.youtube.com/*#*/*',
        'https?://m.youtube.com/index*',
        'https?://*.youtube.com/profile*',
        'https?://*.youtube.com/view_play_list*',
        'https?://*.youtube.com/playlist*'
    ]
}

consumer = oembed.OEmbedConsumer()
for k, v in PROVIDERS.items():
    endpoint = oembed.OEmbedEndpoint(k, v)
    consumer.addEndpoint(endpoint)


def get_oembed_endpoint(url):
    """Return the configured :class:`oembed.OEmbedEndpoint` matching ``url``.

    :returns: The matching endpoint, or ``None`` when no configured provider
        can handle the url (the same condition under which
        ``OEmbedConsumer.embed`` would raise ``OEmbedNoEndpoint``).

    """
    return consumer._endpointFor(url)


def build_oembed_url(endpoint, url):
    """Build the fully-qualified oEmbed request url for ``endpoint``."""
    return endpoint.request(url, format='json')


def parse_oembed_response(raw, content_type):
    """Parse a raw oEmbed response body the same way ``python-oembed`` does.

    Mirrors :meth:`oembed.OEmbedEndpoint.fetch` so requests issued through
    Lassie's own HTTP client keep the exact same response handling.

    :param raw: The response body as decompressed bytes (it is decoded as
        UTF-8 here, after the byte budget has been accounted for).
    :param content_type: The response's ``Content-Type`` header value.
    :raises oembed.OEmbedError: if the mime-type is missing or unsupported.
    :returns: The parsed oEmbed data dictionary.

    """
    text = raw.decode('utf8')

    if not content_type:
        raise oembed.OEmbedError('Missing mime-type in response')

    if content_type.find('application/xml') != -1 or \
       content_type.find('text/xml') != -1:
        response = OEmbedResponse.newFromXML(text)
    elif content_type.find('application/json') != -1 or \
         content_type.find('text/json') != -1:
        response = OEmbedResponse.newFromJSON(text)
    else:
        raise oembed.OEmbedError('Invalid mime-type in response - %s' % content_type)

    return response.getData()


def parse_oembed_data(oembed_data, data):
    """Parse OEmbed response data to inject into lassie's response dict.

    :param oembed_data: OEmbed response data.
    :type oembed_data: dict
    :param data: Refrence to data variable being updated.
    :type data: dict

    """
    data.update({
        'oembed': oembed_data,
    })
    _type = oembed_data.get('type')
    provider_name = oembed_data.get('provider_name')
    if not _type:
        return data

    if oembed_data.get('title'):
        data.update({
            'title': oembed_data.get('title'),
        })

    if _type == 'video':
        try:
            item = {
                'width': convert_to_int(oembed_data.get('width')),
                'height': convert_to_int(oembed_data.get('height'))
            }
            if provider_name in ['YouTube', ]:
                item['src'] = HYPERLINK_PATTERN.search(oembed_data.get('html')).group(0)

            data['videos'].append(item)
        except Exception:
            pass

        if oembed_data.get('thumbnail_url'):
            item = {
                'width': convert_to_int(oembed_data.get('thumbnail_width')),
                'height': convert_to_int(oembed_data.get('thumbnail_height')),
                'src': oembed_data.get('thumbnail_url')
            }

            data['images'].append(item)

    return data
