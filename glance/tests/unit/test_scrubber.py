# vim: tabstop=4 shiftwidth=4 softtabstop=4

# Copyright 2013 Red Hat, Inc.
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

import os
import shutil
import time
import tempfile

import mox

from glance.common import exception
from glance.openstack.common import uuidutils
import glance.store
import glance.store.scrubber
from glance.tests import utils as test_utils


class TestScrubber(test_utils.BaseTestCase):

    def setUp(self):
        self.data_dir = tempfile.mkdtemp()
        self.config(scrubber_datadir=self.data_dir)
        self.config(default_store='file')
        glance.store.create_stores()
        self.mox = mox.Mox()
        super(TestScrubber, self).setUp()

    def tearDown(self):
        self.mox.UnsetStubs()
        shutil.rmtree(self.data_dir)
        super(TestScrubber, self).tearDown()

    def _scrubber_cleanup_with_store_delete_exception(self, ex):
        fname = uuidutils.generate_uuid

        uri = 'file://some/path/%s' % (fname)
        id = 'helloworldid'
        now = time.time()
        scrub = glance.store.scrubber.Scrubber()
        scrub.registry = self.mox.CreateMockAnything()
        self.mox.StubOutWithMock(glance.store, "delete_from_backend")

        scrub.registry.update_image(id, {'status': 'deleted'})
        glance.store.delete_from_backend(
            mox.IgnoreArg(),
            uri).AndRaise(ex)

        self.mox.ReplayAll()
        scrub._delete(id, uri, now)
        self.mox.VerifyAll()

        q_path = os.path.join(self.data_dir, id)
        self.assertFalse(os.path.exists(q_path))

    def test_store_delete_unsupported_backend_exception(self):
        ex = glance.store.UnsupportedBackend()
        self._scrubber_cleanup_with_store_delete_exception(ex)

    def test_store_delete_notfound_exception(self):
        ex = exception.NotFound()
        self._scrubber_cleanup_with_store_delete_exception(ex)
