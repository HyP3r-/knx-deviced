# KNX-DeviceD
This framework is used to develop virtual KNX devices in Python. It offers the possibility to define abstract devices as configuration file and to link them by a corresponding implementation. In the configuration file sensors, actuators and the necessary configuration can be specified. The individual virtual devices can have a defined state which is preserved even over restarts of the software.

## Example
As already described, a KNX virtual device consists of two parts: one or more configuration files and an implementation.

### Configuration
The configuration defines the group addresses, the class and parameters associated with this file.

```
[general]
class = "LogicInverter"
name = "Test"

[sensors]
input = "1/2/3"

[actors]
output = "1/2/4"
```

Furthermore, the project also has a global configuration ([config/core/config.toml](config/core/config.toml)) with which you can store cross-device configuration.

### Class
The class is instantiated with the configuration, the KNX interface and can receive values via the sensor inputs and in turn send values on the bus with the KNX interface.

```
class LogicInverter(device.Device):

    def state_save(self) -> dict:
        pass

    def state_load(self, state: dict):
        pass

    def init(self):
        pass

    def sensor_input(self, packet: knxdclient.ReceivedGroupAPDU):
        _input = knxdclient.decode_value(packet.payload.value, knxdclient.KNXDPT.BOOLEAN)

        await self.connection.group_write(
            util.str_to_group_address(self.device_config["actors"]["output"]),
            knxdclient.KNXDAPDUType.WRITE,
            knxdclient.encode_value(not _input, knxdclient.KNXDPT.BOOLEAN))
```

## Implementations
Currently, two Virtual KNX devices have been implemented so far:
* Logic Inverter
* Automatic Shading (The automatic shading was designed very much according to customer requirements, but should be relatively easy to adjust)

# License
Released under the [MIT License](LICENSE).
