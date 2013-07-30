# Copyright (c) 2010-2012 OpenStack, LLC.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import unittest
import time
import eventlet
from contextlib import contextmanager
from threading import Thread
from webob import Request

from glance.tests.unit.utils import FakeLogger
from glance.api.middleware import ratelimit

"""
Ratelimit middleware unit tests, borrowed from Swift with some modifications to
make it work with Glance.

TODO: add tests for Glance's operation specific limiting.
"""


class FakeMemcache(object):

    def __init__(self):
        self.store = {}
        self.error_on_incr = False
        self.init_incr_return_neg = False

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value, serialize=False, timeout=0):
        self.store[key] = value
        return True

    def incr(self, key, delta=1, timeout=0):
        if self.error_on_incr:
            raise ratelimit.MemcacheConnectionError('Memcache restarting')
        if self.init_incr_return_neg:
            # simulate initial hit, force reset of memcache
            self.init_incr_return_neg = False
            return -10000000
        self.store[key] = int(self.store.setdefault(key, 0)) + int(delta)
        if self.store[key] < 0:
            self.store[key] = 0
        return int(self.store[key])

    def decr(self, key, delta=1, timeout=0):
        return self.incr(key, delta=-delta, timeout=timeout)

    @contextmanager
    def soft_lock(self, key, timeout=0, retries=5):
        yield True

    def delete(self, key):
        try:
            del self.store[key]
        except Exception:
            pass
        return True


def mock_http_connect(response, headers=None, with_exc=False):

    class FakeConn(object):

        def __init__(self, status, headers, with_exc):
            self.status = status
            self.reason = 'Fake'
            self.host = '1.2.3.4'
            self.port = '1234'
            self.with_exc = with_exc
            self.headers = headers
            if self.headers is None:
                self.headers = {}

        def getresponse(self):
            if self.with_exc:
                raise Exception('test')
            return self

        def getheader(self, header):
            return self.headers[header]

        def read(self, amt=None):
            return ''

        def close(self):
            return
    return lambda *args, **kwargs: FakeConn(response, headers, with_exc)


class FakeApp(object):

    def __call__(self, env, start_response):
        return ['204 No Content']


def start_response(*args):
    pass


def dummy_filter_factory(global_conf, **local_conf):
    conf = global_conf.copy()
    conf.update(local_conf)

    def limit_filter(app):
        return ratelimit.RateLimitMiddleware(app, conf, logger=FakeLogger())
    return limit_filter

time_ticker = 0
time_override = []


def mock_sleep(x):
    global time_ticker
    time_ticker += x


def mock_time():
    global time_override
    global time_ticker
    if time_override:
        cur_time = time_override.pop(0)
        if cur_time is None:
            time_override = [None if i is None else i + time_ticker
                             for i in time_override]
            return time_ticker
        return cur_time
    return time_ticker


class TestRateLimit(unittest.TestCase):

    def _reset_time(self):
        global time_ticker
        time_ticker = 0

    def setUp(self):
        self.was_sleep = eventlet.sleep
        eventlet.sleep = mock_sleep
        self.was_time = time.time
        time.time = mock_time
        self._reset_time()

    def tearDown(self):
        eventlet.sleep = self.was_sleep
        time.time = self.was_time

    def _run(self, callable_func, num, rate, check_time=True):
        global time_ticker
        begin = time.time()
        for x in range(0, num):
            result = callable_func()
        end = time.time()
        total_time = float(num) / rate - 1.0 / rate  # 1st request isnt limited
        # Allow for one second of variation in the total time.
        time_diff = abs(total_time - (end - begin))
        if check_time:
            self.assertEquals(round(total_time, 1), round(time_ticker, 1))
        return time_diff

    def test_get_ratelimitable_key_tuples(self):
        account_ratelimit = 10.0
        conf = {'account_ratelimit': account_ratelimit,
                'image_list': 5.0,
                'image_download': 5.0,
                'image_register': 5.0,
                'image_upload': 5.0,
                'image_update': 5.0}
        tok = 'HPAuth10_123456789'
        r = ratelimit.RateLimitMiddleware(None, conf)
        for action, limit in conf.iteritems():
            if action == 'account_ratelimit':
                continue  # not a valid action for this test
            req = Request.blank('/v1/images')
            keys = r.get_ratelimitable_key_tuples(req, tok, action)
            self.assertEqual(keys,
                             [('ratelimit/%s' % tok, account_ratelimit),
                              ('ratelimit/%s/%s' % (tok, action), limit)])

    def test_account_ratelimit(self):
        current_rate = 5
        num_calls = 50
        conf_dict = {'account_ratelimit': current_rate}
        self.test_ratelimit = ratelimit.filter_factory(conf_dict)(FakeApp())
        self.test_ratelimit.log_sleep_time_seconds = .00001
        ratelimit.http_connect = mock_http_connect(204)
        for meth, exp_time in [('DELETE', 9.8), ('GET', 9.8),
                               ('POST', 9.8), ('PUT', 9.8)]:
            req = Request.blank('/v/a%s/c' % meth)
            req.method = meth
            req.headers['x-auth-token'] = 'HPAuth10_123456789%s' % meth
            req.environ['glance.cache'] = FakeMemcache()
            make_app_call = lambda: self.test_ratelimit(req.environ,
                                                        start_response)
            begin = time.time()
            self._run(make_app_call, num_calls, current_rate,
                      check_time=bool(exp_time))
            self.assertEquals(round(time.time() - begin, 1), exp_time)
            self._reset_time()

    def test_ratelimit_set_incr(self):
        current_rate = 5
        num_calls = 50
        conf_dict = {'account_ratelimit': current_rate}
        self.test_ratelimit = ratelimit.filter_factory(conf_dict)(FakeApp())
        ratelimit.http_connect = mock_http_connect(204)
        req = Request.blank('/v/a/c')
        req.headers['x-auth-token'] = 'HPAuth10_123456789'
        req.method = 'PUT'
        req.environ['glance.cache'] = FakeMemcache()
        req.environ['glance.cache'].init_incr_return_neg = True
        make_app_call = lambda: self.test_ratelimit(req.environ,
                                                    start_response)
        begin = time.time()
        self._run(make_app_call, num_calls, current_rate, check_time=False)
        self.assertEquals(round(time.time() - begin, 1), 9.8)

    def test_ratelimit_max_rate_double(self):
        self.skipTest("TODO: make this test work for glance")
        global time_ticker
        global time_override
        current_rate = 2
        conf_dict = {'account_ratelimit': current_rate,
                     'clock_accuracy': 100,
                     'max_sleep_time_seconds': 1}
        self.test_ratelimit = dummy_filter_factory(conf_dict)(FakeApp())
        ratelimit.http_connect = mock_http_connect(204)
        self.test_ratelimit.log_sleep_time_seconds = .00001
        req = Request.blank('/v/a/c')
        req.headers['x-auth-token'] = 'HPAuth10_123456789'
        req.method = 'PUT'
        req.environ['glance.cache'] = FakeMemcache()

        time_override = [0, 0, 0, 0, None]
        # simulates 4 requests coming in at same time, then sleeping
        r = self.test_ratelimit(req.environ, start_response)
        mock_sleep(.1)
        r = self.test_ratelimit(req.environ, start_response)
        mock_sleep(.1)
        r = self.test_ratelimit(req.environ, start_response)
        self.assertEquals(r[0], 'Slow down')
        mock_sleep(.1)
        r = self.test_ratelimit(req.environ, start_response)
        self.assertEquals(r[0], 'Slow down')
        mock_sleep(.1)
        r = self.test_ratelimit(req.environ, start_response)
        self.assertEquals(r[0], '204 No Content')

    def test_ratelimit_max_rate_multiple_acc(self):
        num_calls = 4
        current_rate = 2
        conf_dict = {'account_ratelimit': current_rate,
                     'max_sleep_time_seconds': 2}
        fake_memcache = FakeMemcache()

        the_app = ratelimit.RateLimitMiddleware(None, conf_dict,
                                                logger=FakeLogger())
        the_app.memcache_client = fake_memcache
        req = lambda: None
        req.method = 'PUT'

        class rate_caller(Thread):

            def __init__(self, name):
                self.myname = name
                Thread.__init__(self)

            def run(self):
                for j in range(num_calls):
                    self.result = the_app.handle_ratelimit(req, self.myname,
                                                           'c')

        nt = 15
        begin = time.time()
        threads = []
        for i in range(nt):
            rc = rate_caller('a%s' % i)
            rc.start()
            threads.append(rc)
        for thread in threads:
            thread.join()

        time_took = time.time() - begin
        self.assertEquals(1.5, round(time_took, 1))

    def test_call_invalid_path(self):
        env = {'REQUEST_METHOD': 'GET',
               'SCRIPT_NAME': '',
               'PATH_INFO': '//v1/AUTH_1234567890',
               'SERVER_NAME': '127.0.0.1',
               'SERVER_PORT': '80',
               'glance.cache': FakeMemcache(),
               'SERVER_PROTOCOL': 'HTTP/1.0'}

        app = lambda *args, **kwargs: ['fake_app']
        rate_mid = ratelimit.RateLimitMiddleware(app, {}, logger=FakeLogger())

        class a_callable(object):

            def __call__(self, *args, **kwargs):
                pass
        resp = rate_mid.__call__(env, a_callable())
        self.assert_('fake_app' == resp[0])

    def test_no_memcache(self):
        current_rate = 13
        num_calls = 5
        conf_dict = {'account_ratelimit': current_rate}
        self.test_ratelimit = ratelimit.filter_factory(conf_dict)(FakeApp())
        ratelimit.http_connect = mock_http_connect(204)
        req = Request.blank('/v/a')
        req.headers['x-auth-token'] = 'HPAuth10_123456789'
        req.environ['glance.cache'] = None
        make_app_call = lambda: self.test_ratelimit(req.environ,
                                                    start_response)
        begin = time.time()
        self._run(make_app_call, num_calls, current_rate, check_time=False)
        time_took = time.time() - begin
        self.assertEquals(round(time_took, 1), 0)  # no memcache, no limiting

    def test_restarting_memcache(self):
        current_rate = 2
        num_calls = 5
        conf_dict = {'account_ratelimit': current_rate}
        self.test_ratelimit = ratelimit.filter_factory(conf_dict)(FakeApp())
        ratelimit.http_connect = mock_http_connect(204)
        req = Request.blank('/v/a/c')
        req.headers['x-auth-token'] = 'HPAuth10_123456789'
        req.method = 'PUT'
        req.environ['glance.cache'] = FakeMemcache()
        req.environ['glance.cache'].error_on_incr = True
        make_app_call = lambda: self.test_ratelimit(req.environ,
                                                    start_response)
        begin = time.time()
        self._run(make_app_call, num_calls, current_rate, check_time=False)
        time_took = time.time() - begin
        self.assertEquals(round(time_took, 1), 0)  # no memcache, no limiting

    def test_init_mapper(self):
        rl = ratelimit.RateLimitMiddleware(None, {}, None)
        self.assertTrue(rl.mapper is not None)

    def test_mapper_image_list_or_show(self):
        """
        Test the following 'light weight gets' are properly identified by the
        middleware's mapper:
        - GET /*/images
        - GET /*/images/detail
        - HEAD /*/images/{id}
        """
        rl = ratelimit.RateLimitMiddleware(None, {}, None)
        mapper = rl.mapper

        req = Request.blank('/v1/images')
        self.assertEqual(mapper.match(environ=req.environ),
                         {'action': u'image_list', 'api_version': u'v1'})

        req = Request.blank('/v1/images/detail')
        self.assertEqual(mapper.match(environ=req.environ),
                         {'action': u'image_list', 'api_version': u'v1'})

        test_id = u'00000000-0000-0000-0000-000000000099'
        req = Request.blank('/v1/images/%s' % test_id)
        req.method = 'HEAD'
        self.assertEqual(mapper.match(environ=req.environ),
                         {'action': u'image_list', 'id': test_id,
                          'api_version': u'v1'})

    def test_mapper_image_download(self):
        """
        Test the middleware sucessfully identifies an image download.
        """
        rl = ratelimit.RateLimitMiddleware(None, {}, None)
        mapper = rl.mapper

        test_id = u'00000000-0000-0000-0000-000000000099'
        req = Request.blank('/v1/images/%s' % test_id)
        self.assertEqual(mapper.match(environ=req.environ),
                         {'action': u'image_download', 'id': test_id,
                          'api_version': u'v1'})

    def test_mapper_image_register(self):
        """
        Test the middleware sucessfully identifies an image register (POST with
        no body or location header).
        """
        rl = ratelimit.RateLimitMiddleware(None, {}, None)
        mapper = rl.mapper

        req = Request.blank('/v1/images')
        req.method = 'POST'
        self.assertEqual(mapper.match(environ=req.environ),
                         {'action': u'image_register', 'api_version': u'v1'})

    def test_mapper_image_upload(self):
        """
        Test the middleware sucessfully identifies an image upload.  The
        following operations count as an image upload:
        - POST /*/images with location
        - POST /*/images with body  (location+body will match but isn't a valid
                                     api op so no need to test for this here)
        - PUT /*/images/{id} with body
        """
        rl = ratelimit.RateLimitMiddleware(None, {}, None)
        mapper = rl.mapper

        req = Request.blank('/v1/images')
        req.method = 'POST'
        req.headers['location'] = 'http://awesome-images.com/ubuntu_12.vhd'
        self.assertEqual(mapper.match(environ=req.environ),
                         {'action': u'image_upload', 'api_version': u'v1'})

        req = Request.blank('/v1/images')
        req.method = 'POST'
        req.body = 'foobar'
        self.assertEqual(mapper.match(environ=req.environ),
                         {'action': u'image_upload', 'api_version': u'v1'})

        test_id = u'00000000-0000-0000-0000-000000000099'
        req = Request.blank('/v1/images/%s' % test_id)
        req.method = 'PUT'
        req.body = 'foobar'
        self.assertEqual(mapper.match(environ=req.environ),
                         {'action': u'image_upload', 'id': test_id,
                          'api_version': u'v1'})

    def test_mapper_metadata_put(self):
        """
        Test the middleware sucessfully identifies a metadata put/update.
        """
        rl = ratelimit.RateLimitMiddleware(None, {}, None)
        mapper = rl.mapper

        test_id = u'00000000-0000-0000-0000-000000000099'
        req = Request.blank('/v1/images/%s' % test_id)
        req.method = 'PUT'
        self.assertEqual(mapper.match(environ=req.environ),
                         {'action': u'image_update', 'id': test_id,
                          'api_version': u'v1'})

    def test_get_limit_for_action(self):
        """ Test the correct limit is returned for each allowable action. """
        conf = {'image_list': 5.0,
                'image_download': 5.0,
                'image_register': 5.0,
                'image_upload': 5.0,
                'image_update': 5.0}
        r = ratelimit.RateLimitMiddleware(None, conf)
        for action, limit in conf.iteritems():
            self.assertEqual(r._get_limit_for_action(action), limit)

    def test_get_limit_for_action(self):
        """ Test the a ValueError is raised for invalid actions. """
        r = ratelimit.RateLimitMiddleware(None, {})
        self.assertRaises(ValueError, r._get_limit_for_action, 'foobar')
        self.assertRaises(ValueError, r._get_limit_for_action, 1)
        self.assertRaises(ValueError, r._get_limit_for_action, '_list')
        self.assertRaises(ValueError, r._get_limit_for_action, None)
        self.assertRaises(ValueError, r._get_limit_for_action, {})
        self.assertRaises(ValueError, r._get_limit_for_action, 'image_')


if __name__ == '__main__':
    unittest.main()
