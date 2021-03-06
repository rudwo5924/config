#
# Copyright (c) 2020 Wind River Systems, Inc.
#
# SPDX-License-Identifier: Apache-2.0
#

import pecan
from pecan import rest
import wsme
from wsme import types as wtypes
import wsmeext.pecan as wsme_pecan

from oslo_log import log
from sysinv._i18n import _
from sysinv.api.controllers.v1 import base
from sysinv.api.controllers.v1 import collection
from sysinv.api.controllers.v1 import types
from sysinv.api.controllers.v1 import utils
from sysinv.common import exception
from sysinv.common import device as dconstants
from sysinv.common import utils as cutils
from sysinv import objects

LOG = log.getLogger(__name__)


class DeviceLabelPatchType(types.JsonPatchType):
    @staticmethod
    def mandatory_attrs():
        return []


class DeviceLabel(base.APIBase):
    """API representation of a device label.

    This class enforces type checking and value constraints, and converts
    between the internal object model and the API representation of
    a device label.
    """

    id = int
    "Unique ID for this device label"

    uuid = types.uuid
    "Unique UUID for this device label"

    host_id = int
    "Represent the id of host the device label belongs to"

    host_uuid = types.uuid
    "Represent the uuid of the host the device label belongs to"

    pcidevice_id = int
    "Represent the id of pci_device the device label belongs to"

    pcidevice_uuid = types.uuid
    "Represent the uuid of the pci_device the device label belongs to"

    label_key = wtypes.text
    "Represents a label key assigned to the device"

    label_value = wtypes.text
    "Represents a label value assigned to the device"

    def __init__(self, **kwargs):
        self.fields = list(objects.device_label.fields.keys())
        for k in self.fields:
            setattr(self, k, kwargs.get(k))

        # API-only attribute)
        self.fields.append('action')
        setattr(self, 'action', kwargs.get('action', None))

    @classmethod
    def convert_with_links(cls, rpc_device_label, expand=True):
        device_label = DeviceLabel(**rpc_device_label.as_dict())
        if not expand:
            device_label.unset_fields_except(
                ['uuid', 'host_id', 'host_uuid', 'pcidevice_id', 'pcidevice_uuid',
                 'label_key', 'label_value'])

        # do not expose the id attribute
        device_label.host_id = wtypes.Unset
        device_label.pcidevice_id = wtypes.Unset

        return device_label


class DeviceLabelCollection(collection.Collection):
    """API representation of a collection of device label."""

    device_labels = [DeviceLabel]
    "A list containing device_label objects"

    def __init__(self, **kwargs):
        self._type = 'device_labels'

    @classmethod
    def convert_with_links(cls, rpc_device_labels, limit, url=None,
                           expand=False, **kwargs):
        collection = DeviceLabelCollection()
        collection.device_labels = [DeviceLabel.convert_with_links(p, expand)
                              for p in rpc_device_labels]
        collection.next = collection.get_next(limit, url=url, **kwargs)
        return collection


LOCK_NAME = 'DeviceLabelController'


class DeviceLabelController(rest.RestController):
    """REST controller for device label."""

    def __init__(self, parent=None, **kwargs):
        self._parent = parent

    def _get_device_label_collection(
            self, device_uuid, marker=None, limit=None, sort_key=None,
            sort_dir=None, expand=False, resource_url=None):
        if self._parent and not device_uuid:
            raise exception.InvalidParameterValue(_(
                  "Device id not specified."))

        limit = utils.validate_limit(limit)
        sort_dir = utils.validate_sort_dir(sort_dir)
        marker_obj = None
        if marker:
            marker_obj = objects.device_label.get_by_uuid(
                pecan.request.context,
                marker)

        if device_uuid:
            device_labels = pecan.request.dbapi.device_label_get_by_device(
                                                device_uuid, limit,
                                                marker_obj,
                                                sort_key=sort_key,
                                                sort_dir=sort_dir)
        else:
            device_labels = pecan.request.dbapi.device_label_get_list(
                                                limit, marker_obj,
                                                sort_key=sort_key,
                                                sort_dir=sort_dir)

        return DeviceLabelCollection.convert_with_links(
            device_labels, limit, url=resource_url, expand=expand,
            sort_key=sort_key, sort_dir=sort_dir)

    def _get_one(self, device_label_uuid):
        rpc_device_label = objects.device_label.get_by_uuid(
            pecan.request.context, device_label_uuid)
        return DeviceLabel.convert_with_links(rpc_device_label)

    @wsme_pecan.wsexpose(DeviceLabelCollection, types.uuid, types.uuid,
                         int, wtypes.text, wtypes.text)
    def get_all(self, uuid=None, marker=None, limit=None,
                sort_key='id', sort_dir='asc'):
        """Retrieve a list of device labels."""
        return self._get_device_label_collection(uuid, marker, limit,
                                                 sort_key=sort_key,
                                                 sort_dir=sort_dir)

    @wsme_pecan.wsexpose(DeviceLabel, types.uuid)
    def get_one(self, device_label_uuid):
        """Retrieve a single device label."""

        try:
            sp_label = objects.device_label.get_by_uuid(
                pecan.request.context,
                device_label_uuid)
        except exception.InvalidParameterValue:
            raise wsme.exc.ClientSideError(
                _("No device label found for %s" % device_label_uuid))

        return DeviceLabel.convert_with_links(sp_label)

    @cutils.synchronized(LOCK_NAME)
    @wsme_pecan.wsexpose(DeviceLabelCollection, types.boolean,
                         body=types.apidict)
    def post(self, overwrite=False, body=None):
        """Assign a new device label."""

        label_dict = []
        for d in body:
            k, v = d.popitem()
            if k == 'pcidevice_uuid':
                pcidevice_uuid = v
            else:
                label_dict.append({k: v})

        pcidevice = objects.pci_device.get_by_uuid(pecan.request.context,
                                                   pcidevice_uuid)

        existing_labels = {}
        for lbl in label_dict:
            label_key, label_value = list(lbl.items())[0]
            labels = pecan.request.dbapi.device_label_query(
                pcidevice.id, label_key)
            if len(labels) == 0:
                continue
            if overwrite:
                if len(labels) == 1:
                    existing_labels.update({label_key: labels[0].uuid})
                else:
                    raise wsme.exc.ClientSideError(_(
                        "Cannot overwrite label value as multiple device "
                        "labels exist with label key %s for this device") % label_key)
            elif (len(labels) == 1 and labels[0].label_value == label_value):
                raise wsme.exc.ClientSideError(_(
                    "Device label (%s, %s) already exists for this device") %
                    (label_key, label_value))

        new_records = []
        for lbl in label_dict:
            key, value = list(lbl.items())[0]
            values = {
                'host_id': pcidevice.host_id,
                'pcidevice_id': pcidevice.id,
                'label_key': key,
                'label_value': value
            }
            try:
                if existing_labels.get(key, None):
                    # Label exists, need to remove the existing
                    # device_image_state entries for the old label and
                    # create new entries for the updated label if needed
                    label_uuid = existing_labels.get(key)
                    remove_device_image_state(label_uuid)
                    new_label = pecan.request.dbapi.device_label_update(
                        label_uuid, {'label_value': value})
                else:
                    new_label = pecan.request.dbapi.device_label_create(
                        pcidevice_uuid, values)
                add_device_image_state(pcidevice, key, value)
                new_records.append(new_label)
            except exception.DeviceLabelAlreadyExists:
                # We should not be here
                raise wsme.exc.ClientSideError(_(
                    "Error creating label %s") % label_key)

        return DeviceLabelCollection.convert_with_links(
            new_records, limit=None, url=None, expand=False,
            sort_key='id', sort_dir='asc')

    @cutils.synchronized(LOCK_NAME)
    @wsme_pecan.wsexpose(None, types.uuid, status_code=204)
    def delete(self, device_label_uuid):
        """Delete a device label."""
        remove_device_image_state(device_label_uuid)
        pecan.request.dbapi.device_label_destroy(device_label_uuid)

    @cutils.synchronized(LOCK_NAME)
    @wsme_pecan.wsexpose(DeviceLabel, body=DeviceLabel)
    def patch(self, device_label):
        """Modify a new device label."""
        raise exception.OperationNotPermitted


def remove_device_image_state(device_label_uuid):
    """Cleanup device image state based on device label"""
    device_label = pecan.request.dbapi.device_label_get(device_label_uuid)
    host = pecan.request.dbapi.ihost_get(device_label.host_uuid)
    if host.device_image_update == dconstants.DEVICE_IMAGE_UPDATE_IN_PROGRESS:
        raise wsme.exc.ClientSideError(_(
            "Command rejected: Device image update is in progress for host %s" %
            host.hostname))

    dev_img_states = pecan.request.dbapi.device_image_state_get_all(
        host_id=device_label.host_id,
        pcidevice_id=device_label.pcidevice_id)
    for state in dev_img_states:
        pecan.request.dbapi.device_image_state_destroy(state.id)


def add_device_image_state(pcidevice, key, value):
    """Add device image state based on device label"""
    # If the image has been applied to any devices,
    # create device_image_state entry for this device
    dev_labels = pecan.request.dbapi.device_label_get_by_label(key, value)
    if dev_labels:
        dev_img_lbls = pecan.request.dbapi.device_image_label_get_by_label(
            dev_labels[0].id)
        for img in dev_img_lbls:
            # Create an entry of image to device mapping
            state_values = {
                'host_id': pcidevice.host_id,
                'pcidevice_id': pcidevice.id,
                'image_id': img.image_id,
                'status': dconstants.DEVICE_IMAGE_UPDATE_PENDING,
            }
            pecan.request.dbapi.device_image_state_create(state_values)
