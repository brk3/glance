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

import eventlet
import logging
import routes
import time

from webob import Request, Response

from glance.common.memcached import MemcacheConnectionError

LOG = logging.getLogger('hp.middleware.ratelimit')


class RateLimitMiddleware(object):
    """
    Slimmed down version of Swift's rate limiting middleware.

    Rate limits requests per second based on auth_token.
    (This comes with limitations, but Glance is currently one of the few if
     only services that does not contain tenant-id in the request URI.)

    There are currently two types of rate limits, which can be used in tandem:

    1) x = n, allow n requests of type x per second per auth token.
    2) account_ratelimit = n, allow n total requests per second per auth token.
    """
    HTTP_TOO_MANY_REQUESTS = 429

    def __init__(self, app, conf, logger=None):
        self.app = app
        self.memcache_client = None
        self.clock_accuracy = int(conf.get(
            'clock_accuracy', 1000))
        self.rate_buffer_seconds = int(conf.get(
            'rate_buffer_seconds', 5))
        self.max_sleep_time_seconds = float(conf.get(
            'max_sleep_time_seconds', 60))
        self.log_sleep_time_seconds = float(conf.get(
            'log_sleep_time_seconds', 0))
        self.account_ratelimit = float(conf.get(
            'account_ratelimit', 0))

        self.mapper = routes.Mapper()
        # image-list or image-show (GET/HEAD)
        self.image_list = float(conf.get('image_list', 0))
        self.mapper.connect('/{api_version}/images',
                            conditions=dict(method=['GET']),
                            action='image_list')
        self.mapper.connect('/{api_version}/images/detail',
                            conditions=dict(method=['GET']),
                            action='image_list')
        self.mapper.connect('/{api_version}/images/{id}',
                            conditions=dict(method=['HEAD']),
                            action='image_list')

        # image-download (GET)
        self.image_download = float(conf.get('image_download', 0))
        self.mapper.connect('/{api_version}/images/{id}',
                            conditions=dict(method=['GET']),
                            action='image_download')

        # image-register (POST, no body or location header in request)
        self.image_register = float(conf.get('image_register', 0))
        self.mapper.connect('/{api_version}/images',
                            conditions=dict(
                                method=['POST'],
                                function=lambda env, result:
                                not _has_body_or_location(env, result)),
                            action='image_register')

        # image-upload (PUT/POST, must have body or location header)
        self.image_upload = float(conf.get('image_upload', 0))

        def _has_body_or_location(env, result):
            """ Returns true if there's a match """
            req = Request(env)
            return req.body or req.headers.get('location')
        self.mapper.connect('/{api_version}/images',
                            conditions=dict(method=['POST'],
                                            function=_has_body_or_location),
                            action='image_upload')
        self.mapper.connect('/{api_version}/images/{id}',
                            conditions=dict(method=['PUT'],
                                            function=_has_body_or_location),
                            action='image_upload')

        # image-update (PUT)
        self.image_update = float(conf.get('image_update', 0))
        self.mapper.connect('/{api_version}/images/{id}',
                            conditions=dict(method=['PUT']),
                            action='image_update')

    def get_ratelimitable_key_tuples(self, req, auth_tok, action):
        """
        Returns a list of key (used in memcache), ratelimit tuples. Keys
        should be checked in order.

        :param req: the Request object coming from the wsgi layer
        :param auth_tok: auth token for the request issuer
        :param action: String defining the api action being processed, e.g.
                       'image_download'
        """
        keys = []

        # only allow n total limitable requests per second
        limitable_methods = ('GET', 'PUT', 'POST', 'DELETE', 'HEAD')
        if req.method in limitable_methods and self.account_ratelimit:
            keys.append(("ratelimit/%s" % auth_tok, self.account_ratelimit))

        # set individual limits based on the action
        limit = 0
        try:
            limit = self._get_limit_for_action(action)
        except ValueError as e:
            LOG.error(e)
        if limit > 0:
            keys.append(("ratelimit/%s/%s" % (auth_tok, action), limit))

        return keys

    def _get_limit_for_action(self, action):
        """
        Returns the limit per second for a rate limitable action.

        :param action: string defining a rate limitable action.
        :raises ValueError: if an unknown action is passed in.
        """
        limit = None
        if action == 'image_list':
            limit = self.image_list
        if action == 'image_download':
            limit = self.image_download
        if action == 'image_register':
            limit = self.image_register
        if action == 'image_upload':
            limit = self.image_upload
        if action == 'image_update':
            limit = self.image_update
        if limit is None:
            raise ValueError("Unknown action in _get_limit_for_action: %s" %
                             action)
        return limit

    def _get_sleep_time(self, key, max_rate):
        '''
        Returns the amount of time (a float in seconds) that the app
        should sleep.

        :param key: a memcache key
        :param max_rate: maximum rate allowed in requests per second
        :raises: MaxSleepTimeHitError if max sleep time is exceeded.
        '''
        try:
            now_m = int(round(time.time() * self.clock_accuracy))
            time_per_request_m = int(round(self.clock_accuracy / max_rate))
            running_time_m = self.memcache_client.incr(
                key, delta=time_per_request_m)
            need_to_sleep_m = 0
            if (now_m - running_time_m >
                    self.rate_buffer_seconds * self.clock_accuracy):
                next_avail_time = int(now_m + time_per_request_m)
                self.memcache_client.set(key, str(next_avail_time),
                                         serialize=False)
            else:
                need_to_sleep_m = \
                    max(running_time_m - now_m - time_per_request_m, 0)

            max_sleep_m = self.max_sleep_time_seconds * self.clock_accuracy
            if max_sleep_m - need_to_sleep_m <= self.clock_accuracy * 0.01:
                # treat as no-op decrement time
                self.memcache_client.decr(key, delta=time_per_request_m)
                raise MaxSleepTimeHitError(
                    "Max Sleep Time Exceeded: %.2f" %
                    (float(need_to_sleep_m) / self.clock_accuracy))

            return float(need_to_sleep_m) / self.clock_accuracy
        except MemcacheConnectionError as e:
            LOG.error(e)
            return 0

    def handle_ratelimit(self, req, auth_tok, action):
        '''
        Performs rate limiting by sleeping the current eventlet thread if
        necessary.  If the time to sleep exceeds log_sleep_time_seconds HTTP
        429 is returned.

        :param req: the Request object coming from the wsgi layer
        :param auth_tok: auth token for the request issuer
        :param action: String defining the api action being processed, e.g.
                       'image_download'
        '''
        if not self.memcache_client:
            LOG.error("handle_ratelimit: memcache_client is None")
            return None
        limits = self.get_ratelimitable_key_tuples(req, auth_tok, action)
        for key, max_rate in limits:
            try:
                need_to_sleep = self._get_sleep_time(key, max_rate)
                if (self.log_sleep_time_seconds and
                        need_to_sleep > self.log_sleep_time_seconds):
                    LOG.warning(_("Ratelimit sleep log: %s for %s" %
                                  (need_to_sleep, key)))
                if need_to_sleep > 0:
                    eventlet.sleep(need_to_sleep)
            except MaxSleepTimeHitError, e:
                LOG.warning(_("Returning %d for %s" %
                            (self.HTTP_TOO_MANY_REQUESTS, key)))
                error_resp = Response(status='%d Rate Limited' %
                                      self.HTTP_TOO_MANY_REQUESTS,
                                      body='Slow down', request=req)
                return error_resp
        return None

    def __call__(self, env, start_response):
        """
        WSGI entry point.
        Wraps env in webob.Request object and passes it down.

        :param env: WSGI environment dictionary
        :param start_response: WSGI callable
        """
        req = Request(env)
        if self.memcache_client is None:
            self.memcache_client = cache_from_env(env)
        if not self.memcache_client:
            LOG.warning(
                _('Warning: Cannot ratelimit without a memcached client'))
            return self.app(env, start_response)

        account = req.headers.get('x-auth-token')
        if account is None:
            LOG.info(_('No x-auth-token found in headers, bypassing '
                       'ratelimit'))
            return self.app(env, start_response)

        request_info = self.mapper.match(environ=env)
        action = None
        if request_info:
            action = request_info.get('action')
        if action:
            LOG.debug("Request contains rate limitable action: '%s' " % action)
        ratelimit_resp = self.handle_ratelimit(req, account, action)
        if ratelimit_resp is None:
            return self.app(env, start_response)
        else:
            return ratelimit_resp(env, start_response)


def filter_factory(global_conf, **local_conf):
    """
    paste.deploy app factory for creating WSGI proxy apps.
    """
    conf = global_conf.copy()
    conf.update(local_conf)

    def limit_filter(app):
        return RateLimitMiddleware(app, conf)
    return limit_filter


# NOTE(bourke): copied from swift.common.utils
def item_from_env(env, item_name):
    """
    Get a value from the wsgi environment

    :param env: wsgi environment dict
    :param item_name: name of item to get

    :returns: the value from the environment
    """
    item = env.get(item_name, None)
    if item is None:
        LOG.error("ERROR: %s could not be found in env!" % item_name)
    return item


# NOTE(bourke): copied from swift.common.utils
def cache_from_env(env):
    """
    Get memcache connection pool from the environment (which had been
    previously set by the memcache middleware

    :param env: wsgi environment dict

    :returns: glance.common.memcached.MemcacheRing from environment
    """
    return item_from_env(env, 'glance.cache')


class MaxSleepTimeHitError(Exception):
    pass
