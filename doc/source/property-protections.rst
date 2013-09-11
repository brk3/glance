..
      Copyright 2013 OpenStack Foundation
      All Rights Reserved.

      Licensed under the Apache License, Version 2.0 (the "License"); you may
      not use this file except in compliance with the License. You may obtain
      a copy of the License at

          http://www.apache.org/licenses/LICENSE-2.0

      Unless required by applicable law or agreed to in writing, software
      distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
      WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
      License for the specific language governing permissions and limitations
      under the License.

Property Protections
====================

There are two types of image properties in Glance:

* Core Properties, as specified by the image schema.
* Meta Properties, which are arbitrary key/value pairs that can be added to an
   image.

Access to both forms of properties through Glance's public API calls may be
restricted to certain sets of users, using a property protections configuration
file.

This document explains exactly how property protections are configured and what
they apply to.


Constructing a Property Protections Configuration File
------------------------------------------------------

A property protections configuration file follows the format of the Glance API
configuration file, which consists of sections, led by a ``[section]`` header
and followed by ``name: value`` entries.  Each section header is a regular
expression matching a set of properties to be protected.

.. note::

  Section headers must compile to a valid regular expression, otherwise a **500
  Internal Server Error** will be thrown on server startup.

Each section describes four key-value pairs, where the key is one of
``create/read/update/delete``, and the value is a comma separated list of user
roles that are permitted to perform that action in the Glance API.

Property protections are applied in the order specified in the configuration
file.  This means that if for example you specify a section with ``[.*]`` at
the top of the file, all proceeding sections will be ignored.

If an operation is misspelt or omitted, that operation will be disabled for
all roles.

Examples
--------

Example 1. Limit all property interactions to admin only.

 ::

  [.*]
  create = admin
  read = admin
  update = admin
  delete = admin

Example 2. Allow both admins and regular users to modify any properties
prefixed with ``x_``.  Limit regular users to read only for anything else.

 ::

  [^x_.*]
  create = admin,member
  read = admin,member
  update = admin,member
  delete = admin,member

  [.*]
  create = admin
  read = admin,member
  update = admin
  delete = admin
