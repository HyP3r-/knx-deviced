import knxdclient

import device


class AutomaticShading(device.Device):

    def state_save(self) -> dict:
        pass

    def state_load(self, state: dict):
        pass

    def init(self):
        pass

    def sensor_enable(self, packet: knxdclient.ReceivedGroupAPDU):
        pass
