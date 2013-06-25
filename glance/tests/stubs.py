# vim: tabstop=4 shiftwidth=4 softtabstop=4

# Copyright 2010-2011 OpenStack, LLC
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

"""Stubouts, mocks and fixtures for the test suite"""

import os
import routes
import webob

from glance.api.middleware import context
from glance.api.v1 import router
import glance.common.client
from glance.registry.api import v1 as rserver
from glance.tests import utils


VERBOSE = False
DEBUG = False


class FakeRegistryConnection(object):

    def __init__(self, registry=None):
        self.registry = registry or rserver

    def __call__(self, *args, **kwargs):
        # NOTE(flaper87): This method takes
        # __init__'s place in the chain.
        return self

    def connect(self):
        return True

    def close(self):
        return True

    def request(self, method, url, body=None, headers=None):
        self.req = webob.Request.blank("/" + url.lstrip("/"))
        self.req.method = method
        if headers:
            self.req.headers = headers
        if body:
            self.req.body = body

    def getresponse(self):
        mapper = routes.Mapper()
        server = self.registry.API(mapper)
        # NOTE(markwash): we need to pass through context auth information if
        # we have it.
        if 'X-Auth-Token' in self.req.headers:
            api = utils.FakeAuthMiddleware(server)
        else:
            api = context.UnauthenticatedContextMiddleware(server)
        webob_res = self.req.get_response(api)

        return utils.FakeHTTPResponse(status=webob_res.status_int,
                                      headers=webob_res.headers,
                                      data=webob_res.body)


def stub_out_registry_and_store_server(stubs, base_dir, **kwargs):
    """
    Mocks calls to 127.0.0.1 on 9191 and 9292 for testing so
    that a real Glance server does not need to be up and
    running
    """

    class FakeSocket(object):

        def __init__(self, *args, **kwargs):
            pass

        def fileno(self):
            return 42

    class FakeGlanceConnection(object):

        def __init__(self, *args, **kwargs):
            self.sock = FakeSocket()

        def connect(self):
            return True

        def close(self):
            return True

        def _clean_url(self, url):
            #TODO(bcwaldon): Fix the hack that strips off v1
            return url.replace('/v1', '', 1) if url.startswith('/v1') else url

        def putrequest(self, method, url):
            self.req = webob.Request.blank(self._clean_url(url))
            self.req.method = method

        def putheader(self, key, value):
            self.req.headers[key] = value

        def endheaders(self):
            hl = [i.lower() for i in self.req.headers.keys()]
            assert not ('content-length' in hl and
                        'transfer-encoding' in hl), \
                'Content-Length and Transfer-Encoding are mutually exclusive'

        def send(self, data):
            # send() is called during chunked-transfer encoding, and
            # data is of the form %x\r\n%s\r\n. Strip off the %x and
            # only write the actual data in tests.
            self.req.body += data.split("\r\n")[1]

        def request(self, method, url, body=None, headers=None):
            self.req = webob.Request.blank(self._clean_url(url))
            self.req.method = method
            if headers:
                self.req.headers = headers
            if body:
                self.req.body = body

        def getresponse(self):
            mapper = routes.Mapper()
            api = context.UnauthenticatedContextMiddleware(router.API(mapper))
            res = self.req.get_response(api)

            # httplib.Response has a read() method...fake it out
            def fake_reader():
                return res.body

            setattr(res, 'read', fake_reader)
            return res

    def fake_get_connection_type(client):
        """
        Returns the proper connection type
        """
        DEFAULT_REGISTRY_PORT = 9191
        DEFAULT_API_PORT = 9292

        if (client.port == DEFAULT_API_PORT and
            client.host == '0.0.0.0'):
            return FakeGlanceConnection
        elif (client.port == DEFAULT_REGISTRY_PORT and
              client.host == '0.0.0.0'):
            rserver = kwargs.get("registry", None)
            return FakeRegistryConnection(registry=rserver)

    def fake_image_iter(self):
        for i in self.source.app_iter:
            yield i

    stubs.Set(glance.common.client.BaseClient, 'get_connection_type',
              fake_get_connection_type)


def stub_out_registry_server(stubs, **kwargs):
    """
    Mocks calls to 127.0.0.1 on 9191 for testing so
    that a real Glance Registry server does not need to be up and
    running
    """
    def fake_get_connection_type(client):
        """
        Returns the proper connection type
        """
        DEFAULT_REGISTRY_PORT = 9191

        if (client.port == DEFAULT_REGISTRY_PORT and
            client.host == '0.0.0.0'):
            rserver = kwargs.pop("registry", None)
            return FakeRegistryConnection(registry=rserver)

    def fake_image_iter(self):
        for i in self.response.app_iter:
            yield i

    stubs.Set(glance.common.client.BaseClient, 'get_connection_type',
              fake_get_connection_type)
