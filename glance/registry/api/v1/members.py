# vim: tabstop=4 shiftwidth=4 softtabstop=4

# Copyright 2010-2011 OpenStack LLC.
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

import webob.exc

from glance.common import exception
from glance.common import utils
from glance.common import wsgi
import glance.db
import glance.openstack.common.log as logging


LOG = logging.getLogger(__name__)


class Controller(object):

    def _check_can_access_image_members(self, context):
        if context.owner is None and not context.is_admin:
            raise webob.exc.HTTPUnauthorized(_("No authenticated user"))

    def __init__(self):
        self.db_api = glance.db.get_api()
        self.db_api.configure_db()

    def index(self, req, image_id):
        """
        Get the members of an image.
        """
        try:
            self.db_api.image_get(req.context, image_id)
        except exception.NotFound:
            raise webob.exc.HTTPNotFound()
        except exception.Forbidden:
            # If it's private and doesn't belong to them, don't let on
            # that it exists
            msg = _("Access by %(user)s to image %(id)s "
                    "denied") % ({'user': req.context.user,
                    'id': image_id})
            LOG.info(msg)
            raise webob.exc.HTTPNotFound()

        members = self.db_api.image_member_find(req.context, image_id=image_id)

        return dict(members=make_member_list(members,
                                             member_id='member',
                                             can_share='can_share'))

    @utils.mutating
    def update_all(self, req, image_id, body):
        """
        Replaces the members of the image with those specified in the
        body.  The body is a dict with the following format::

            {"memberships": [
                {"member_id": <MEMBER_ID>,
                 ["can_share": [True|False]]}, ...
            ]}
        """
        self._check_can_access_image_members(req.context)

        # Make sure the image exists
        session = self.db_api.get_session()
        try:
            image = self.db_api.image_get(req.context, image_id,
                                          session=session)
        except exception.NotFound:
            raise webob.exc.HTTPNotFound()
        except exception.Forbidden:
            # If it's private and doesn't belong to them, don't let on
            # that it exists
            msg = _("Access by %(user)s to image %(id)s "
                    "denied") % ({'user': req.context.user,
                    'id': image_id})
            LOG.info(msg)
            raise webob.exc.HTTPNotFound()

        # Can they manipulate the membership?
        if not self.db_api.is_image_sharable(req.context, image):
            msg = _("No permission to share that image")
            raise webob.exc.HTTPForbidden(msg)

        # Get the membership list
        try:
            memb_list = body['memberships']
        except Exception, e:
            # Malformed entity...
            msg = _("Invalid membership association: %s") % e
            raise webob.exc.HTTPBadRequest(explanation=msg)

        add = []
        existing = {}
        # Walk through the incoming memberships
        for memb in memb_list:
            try:
                datum = dict(image_id=image['id'],
                             member=memb['member_id'],
                             can_share=None)
            except Exception, e:
                # Malformed entity...
                msg = _("Invalid membership association: %s") % e
                raise webob.exc.HTTPBadRequest(explanation=msg)

            # Figure out what can_share should be
            if 'can_share' in memb:
                datum['can_share'] = bool(memb['can_share'])

            # Try to find the corresponding membership
            members = self.db_api.image_member_find(req.context,
                                                    image_id=datum['image_id'],
                                                    member=datum['member'],
                                                    session=session)
            try:
                member = members[0]
            except IndexError:
                # Default can_share
                datum['can_share'] = bool(datum['can_share'])
                add.append(datum)
            else:
                # Are we overriding can_share?
                if datum['can_share'] is None:
                    datum['can_share'] = members[0]['can_share']

                existing[member['id']] = {
                    'values': datum,
                    'membership': member,
                }

        # We now have a filtered list of memberships to add and
        # memberships to modify.  Let's start by walking through all
        # the existing image memberships...
        existing_members = self.db_api.image_member_find(req.context,
                                                         image_id=image['id'])
        for memb in existing_members:
            if memb['id'] in existing:
                # Just update the membership in place
                update = existing[memb['id']]['values']
                self.db_api.image_member_update(req.context, memb, update,
                                                session=session)
            else:
                # Outdated one; needs to be deleted
                self.db_api.image_member_delete(req.context, memb,
                                                session=session)

        # Now add the non-existent ones
        for memb in add:
            self.db_api.image_member_create(req.context, memb, session=session)

        # Make an appropriate result
        return webob.exc.HTTPNoContent()

    @utils.mutating
    def update(self, req, image_id, id, body=None):
        """
        Adds a membership to the image, or updates an existing one.
        If a body is present, it is a dict with the following format::

            {"member": {
                "can_share": [True|False]
            }}

        If "can_share" is provided, the member's ability to share is
        set accordingly.  If it is not provided, existing memberships
        remain unchanged and new memberships default to False.
        """
        self._check_can_access_image_members(req.context)

        # Make sure the image exists
        try:
            image = self.db_api.image_get(req.context, image_id)
        except exception.NotFound:
            raise webob.exc.HTTPNotFound()
        except exception.Forbidden:
            # If it's private and doesn't belong to them, don't let on
            # that it exists
            msg = _("Access by %(user)s to image %(id)s "
                    "denied") % ({'user': req.context.user,
                    'id': image_id})
            LOG.info(msg)
            raise webob.exc.HTTPNotFound()

        # Can they manipulate the membership?
        if not self.db_api.is_image_sharable(req.context, image):
            msg = _("No permission to share that image")
            raise webob.exc.HTTPForbidden(msg)

        # Determine the applicable can_share value
        can_share = None
        if body:
            try:
                can_share = bool(body['member']['can_share'])
            except Exception, e:
                # Malformed entity...
                msg = _("Invalid membership association: %s") % e
                raise webob.exc.HTTPBadRequest(explanation=msg)

        # Look up an existing membership...
        session = self.db_api.get_session()
        members = self.db_api.image_member_find(req.context,
                                                image_id=image_id,
                                                member=id,
                                                session=session)
        if members:
            if can_share is not None:
                values = dict(can_share=can_share)
                self.db_api.image_member_update(req.context, members[0],
                                                values, session=session)
        else:
            values = dict(image_id=image['id'], member=id,
                          can_share=bool(can_share))
            self.db_api.image_member_create(req.context, values,
                                            session=session)

        return webob.exc.HTTPNoContent()

    @utils.mutating
    def delete(self, req, image_id, id):
        """
        Removes a membership from the image.
        """
        self._check_can_access_image_members(req.context)

        # Make sure the image exists
        try:
            image = self.db_api.image_get(req.context, image_id)
        except exception.NotFound:
            raise webob.exc.HTTPNotFound()
        except exception.Forbidden:
            # If it's private and doesn't belong to them, don't let on
            # that it exists
            msg = _("Access by %(user)s to image %(id)s "
                    "denied") % ({'user': req.context.user,
                    'id': image_id})
            LOG.info(msg)
            raise webob.exc.HTTPNotFound()

        # Can they manipulate the membership?
        if not self.db_api.is_image_sharable(req.context, image):
            msg = _("No permission to share that image")
            raise webob.exc.HTTPForbidden(msg)

        # Look up an existing membership
        try:
            session = self.db_api.get_session()
            members = self.db_api.image_member_find(req.context,
                                                    image_id=image_id,
                                                    member=id,
                                                    session=session)
            self.db_api.image_member_delete(req.context,
                                            members[0],
                                            session=session)
        except exception.NotFound:
            pass

        # Make an appropriate result
        return webob.exc.HTTPNoContent()

    def index_shared_images(self, req, id):
        """
        Retrieves images shared with the given member.
        """
        try:
            members = self.db_api.image_member_find(req.context, member=id)
        except exception.NotFound, e:
            msg = _("Membership could not be found.")
            raise webob.exc.HTTPBadRequest(explanation=msg)

        return dict(shared_images=make_member_list(members,
                                                   image_id='image_id',
                                                   can_share='can_share'))


def make_member_list(members, **attr_map):
    """
    Create a dict representation of a list of members which we can use
    to serialize the members list.  Keyword arguments map the names of
    optional attributes to include to the database attribute.
    """

    def _fetch_memb(memb, attr_map):
        return dict([(k, memb[v]) for k, v in attr_map.items()
                                  if v in memb.keys()])

    # Return the list of members with the given attribute mapping
    return [_fetch_memb(memb, attr_map) for memb in members
            if not memb.deleted]


def action(self, req, id, body):
    """Restores image pending deletion.

    :param req: Request body.  Ignored.
    :param id:  The opaque internal identifier for the image

    :retval Returns the updated image information as a mapping,

    """
    context = None
    action = body['action']
    if action == 'restore':
        try:
            logger.debug("Restoring image %(id)s" % locals())
            db_api.image_restore(context, id)
            return ''
        except exception.Invalid, e:
            msg = "Failed to restore image."
            logger.error(msg)
            return exc.HTTPBadRequest(msg)
        except exception.NotFound:
            raise exc.HTTPNotFound(body='Image not found',
                                   request=req,
                                   content_type='text/plain')
    else:
        msg = "Unknown action %s." % action
        logger.error(msg)
        return exc.HTTPBadRequest(msg)


def create_resource():
    """Image members resource factory method."""
    deserializer = wsgi.JSONRequestDeserializer()
    serializer = wsgi.JSONResponseSerializer()
    return wsgi.Resource(Controller(), deserializer, serializer)
