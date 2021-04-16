import knxdclient

import device
import util


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
