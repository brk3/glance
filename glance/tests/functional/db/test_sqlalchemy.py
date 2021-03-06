# Copyright 2012 OpenStack Foundation
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


import glance.db.sqlalchemy.api
from glance.db.sqlalchemy import models as db_models
import glance.tests.functional.db as db_tests
from glance.tests.functional.db import base


def get_db(config):
    config(sql_connection='sqlite://', verbose=False, debug=False)
    db_api = glance.db.sqlalchemy.api
    db_api.setup_db_env()
    db_api.get_engine()
    return db_api


def reset_db(db_api):
    db_models.unregister_models(db_api._ENGINE)
    db_models.register_models(db_api._ENGINE)


class TestSqlAlchemyDriver(base.TestDriver, base.DriverTests):

    def setUp(self):
        db_tests.load(get_db, reset_db)
        super(TestSqlAlchemyDriver, self).setUp()
        self.addCleanup(db_tests.reset)


class TestSqlAlchemyVisibility(base.TestVisibility, base.VisibilityTests):

    def setUp(self):
        db_tests.load(get_db, reset_db)
        super(TestSqlAlchemyVisibility, self).setUp()
        self.addCleanup(db_tests.reset)


class TestSqlAlchemyMembershipVisibility(base.TestMembershipVisibility,
                                         base.MembershipVisibilityTests):

    def setUp(self):
        db_tests.load(get_db, reset_db)
        super(TestSqlAlchemyMembershipVisibility, self).setUp()
        self.addCleanup(db_tests.reset)


class TestSqlAlchemyDBDataIntegrity(base.TestDriver):
    """ Test class for checking the data integrity in the database.
    Helpful in testing scenarios specific to the sqlalchemy api.
    """

    def setUp(self):
        db_tests.load(get_db, reset_db)
        super(TestSqlAlchemyDBDataIntegrity, self).setUp()
        self.addCleanup(db_tests.reset)

    def test_paginate_redundant_sort_keys(self):
        original_method = self.db_api._paginate_query

        def fake_paginate_query(query, model, limit,
                                sort_keys, marker, sort_dir):
            self.assertEquals(sort_keys, ['created_at', 'id'])
            return original_method(query, model, limit,
                                   sort_keys, marker, sort_dir)

        self.stubs.Set(self.db_api, '_paginate_query',
                       fake_paginate_query)
        images = self.db_api.image_get_all(self.context,
                                           sort_key='created_at')

    def test_paginate_non_redundant_sort_keys(self):
        original_method = self.db_api._paginate_query

        def fake_paginate_query(query, model, limit,
                                sort_keys, marker, sort_dir):
            self.assertEquals(sort_keys, ['name', 'created_at', 'id'])
            return original_method(query, model, limit,
                                   sort_keys, marker, sort_dir)

        self.stubs.Set(self.db_api, '_paginate_query',
                       fake_paginate_query)
        images = self.db_api.image_get_all(self.context,
                                           sort_key='name')
