import knxdclient

import device


class AutomaticShading(device.Device):

    def state_save(self) -> dict:
        pass

    def state_load(self, state: dict):
        pass

    def init(self):
        pass

    async def sensor_enable(self, packet: knxdclient.ReceivedGroupAPDU):
        print("Received group telegram: {}".format(packet))
        pass
