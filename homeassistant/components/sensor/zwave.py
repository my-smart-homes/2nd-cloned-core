"""
homeassistant.components.sensor.zwave
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Interfaces with Z-Wave sensors.
"""
import homeassistant.components.zwave as zwave
from homeassistant.helpers import Device
from homeassistant.const import (
    ATTR_FRIENDLY_NAME, ATTR_BATTERY_LEVEL, ATTR_UNIT_OF_MEASUREMENT,
    TEMP_CELCIUS, TEMP_FAHRENHEIT, ATTR_LOCATION, STATE_ON, STATE_OFF)


class ZWaveSensor(Device):
    """ Represents a Z-Wave sensor. """
    def __init__(self, sensor_value):
        self._value = sensor_value
        self._node = sensor_value.node

    @property
    def unique_id(self):
        """ Returns a unique id. """
        return "ZWAVE-{}-{}".format(self._node.node_id, self._value.object_id)

    @property
    def name(self):
        """ Returns the name of the device. """
        name = self._node.name or "{} {}".format(
            self._node.manufacturer_name, self._node.product_name)

        return "{} {}".format(name, self._value.label)

    @property
    def state(self):
        """ Returns the state of the sensor. """
        return self._value.data

    @property
    def state_attributes(self):
        """ Returns the state attributes. """
        attrs = {
            ATTR_FRIENDLY_NAME: self.name,
            zwave.ATTR_NODE_ID: self._node.node_id,
        }

        battery_level = self._node.get_battery_level()

        if battery_level is not None:
            attrs[ATTR_BATTERY_LEVEL] = battery_level

        unit = self.unit

        if unit:
            attrs[ATTR_UNIT_OF_MEASUREMENT] = unit

        location = self._node.location

        if location:
            attrs[ATTR_LOCATION] = location

        return attrs

    @property
    def unit(self):
        """ Unit if sensor has one. """
        return self._value.units


# pylint: disable=too-few-public-methods
class ZWaveBinarySensor(ZWaveSensor):
    """ Represents a binary sensor within Z-Wave. """

    @property
    def state(self):
        """ Returns the state of the sensor. """
        return STATE_ON if self._value.data else STATE_OFF


class ZWaveMultilevelSensor(ZWaveSensor):
    """ Represents a multi level sensor Z-Wave sensor. """

    @property
    def state(self):
        """ Returns the state of the sensor. """
        value = self._value.data

        if self._value.units in ('C', 'F'):
            return round(value, 1)

        return value

    @property
    def unit(self):
        """ Unit of this sensor. """
        unit = self._value.units

        if unit == 'C':
            return TEMP_CELCIUS
        elif unit == 'F':
            return TEMP_FAHRENHEIT
        else:
            return unit


def devices_discovered(hass, config, info):
    """ Called when a device is discovered. """
    node = zwave.NETWORK.nodes[info[zwave.ATTR_NODE_ID]]
    value = node.values[info[zwave.ATTR_VALUE_ID]]

    value.set_change_verified(False)

    if zwave.NETWORK.controller.node_id not in node.groups[1].associations:
        node.groups[1].add_association(zwave.NETWORK.controller.node_id)

    if value.command_class == zwave.COMMAND_CLASS_SENSOR_BINARY:
        return [ZWaveBinarySensor(value)]

    elif value.command_class == zwave.COMMAND_CLASS_SENSOR_MULTILEVEL:
        return [ZWaveMultilevelSensor(value)]
