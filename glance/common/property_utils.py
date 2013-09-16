# vim: tabstop=4 shiftwidth=4 softtabstop=4

#    Copyright 2013 Rackspace
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

import ConfigParser
import ordereddict
import re

from oslo.config import cfg
import webob.exc

from glance.openstack.common import log as logging

# NOTE(bourke): The default dict_type is collections.OrderedDict in py27, but
# we must set manually for compatibility with py26
CONFIG = ConfigParser.SafeConfigParser(dict_type=ordereddict.OrderedDict)
LOG = logging.getLogger(__name__)

property_opts = [
    cfg.StrOpt('property_protection_file',
               default=None,
               help=_('The location of the property protection file.')),
]

CONF = cfg.CONF
CONF.register_opts(property_opts)


def is_property_protection_enabled():
    if CONF.property_protection_file:
        return True
    return False


class PropertyRules(object):

    def __init__(self):
        self.rules = []
        self._load_rules()

    def _load_rules(self):
        try:
            conf_file = CONF.find_file(CONF.property_protection_file)
            CONFIG.read(conf_file)
        except Exception as e:
            msg = _("Couldn't find property protection file %s:%s." %
                    (CONF.property_protection_file, e))
            LOG.error(msg)
            raise webob.exc.HTTPInternalServerError(explanation=msg)

        operations = ['create', 'read', 'update', 'delete']
        properties = CONFIG.sections()
        for property_exp in properties:
            property_dict = {}
            compiled_rule = self._compile_rule(property_exp)

            for operation in operations:
                roles = CONFIG.get(property_exp, operation)
                if roles:
                    roles = [role.strip() for role in roles.split(',')]
                    property_dict[operation] = roles
                else:
                    property_dict[operation] = []
                    msg = _(('Property protection on operation %s for rule '
                            '%s is not found. No role will be allowed to '
                            'perform this operation.' %
                            (operation, property_exp)))
                    LOG.warn(msg)

            self.rules.append((compiled_rule, property_dict))

    def _compile_rule(self, rule):
        try:
            return re.compile(rule)
        except Exception as e:
            msg = _("Encountered a malformed property protection rule %s:%s."
                    % (rule, e))
            LOG.error(msg)
            raise webob.exc.HTTPInternalServerError(explanation=msg)

    def check_property_rules(self, property_name, action, roles):
        if not self.rules:
            return True

        if action not in ['create', 'read', 'update', 'delete']:
            return False

        for rule_exp, rule in self.rules:
            if rule_exp.search(str(property_name)):
                permitted_roles = rule.get(action)
                if '@' in permitted_roles and '!' in permitted_roles:
                    msg = _("Malformed property protection rule '%s': '@' and "
                            "'!' are mutually exclusive" % property_name)
                    LOG.error(msg)
                    raise webob.exc.HTTPInternalServerError(msg)
                elif '@' in permitted_roles:
                    return True
                elif '!' in permitted_roles:
                    return False
                elif set(roles).intersection(set(permitted_roles)):
                    return True
        return False
