import asyncio
from enum import Enum

import knxdclient
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from astral import LocationInfo
from astral.sun import sun

import device
import util


class AutomaticShadingState(Enum):
    NIGHT = 0
    DAY = 1
    SHADING = 2


class AutomaticShadingDayNight(Enum):
    NIGHT = 0
    DAY = 1


class AutomaticShading(device.Device):
    def __init__(self, connection: knxdclient.KNXDConnection, loop: asyncio.AbstractEventLoop,
                 scheduler: AsyncIOScheduler, device_config: dict, core_config: dict):
        super().__init__(connection, loop, scheduler, device_config, core_config)
        self.enabled = False
        self.automatic_shading_state = AutomaticShadingState.NIGHT
        self.location_info = LocationInfo(self.core_config["location"]["name"],
                                          self.core_config["location"]["region"],
                                          self.core_config["location"]["timezone"],
                                          self.core_config["location"]["latitude"],
                                          self.core_config["location"]["longitude"])

    async def state_save(self) -> dict:
        pass

    async def state_load(self, state: dict):
        pass

    async def init(self):
        """
        Init System
        """

        await self.schedule_day_night()

    async def schedule_day_night(self):
        """
        Schedule next sunrise or sunset job
        """

        s = sun(self.location_info.observer, tzinfo=self.location_info.tzinfo)
        dusk, dawn = s["dusk"], s["dawn"]
        day_night = AutomaticShadingDayNight.DAY if dawn > dusk else AutomaticShadingDayNight.NIGHT
        date = dawn if dawn > dusk else dusk
        self.scheduler.add_job(self.begin_day_night, "date", run_date=date, args=(day_night,))

    async def begin_day_night(self, day_night: AutomaticShadingDayNight):
        """

        """

        if not self.enabled:
            return

        if day_night == AutomaticShadingDayNight.DAY:
            position_height, position_slat = 0, 0
        else:
            position_height, position_slat = 100, 100

        await self.connection.group_write(util.str_to_group_address(self.device_config["actors"]["position-height"]),
                                          knxdclient.KNXDAPDUType.WRITE,
                                          knxdclient.encode_value(util.percentage_to_int(position_height),
                                                                  knxdclient.KNXDPT.UINT8))
        await self.connection.group_write(util.str_to_group_address(self.device_config["actors"]["position-slat"]),
                                          knxdclient.KNXDAPDUType.WRITE,
                                          knxdclient.encode_value(util.percentage_to_int(position_slat),
                                                                  knxdclient.KNXDPT.UINT8))
        await self.schedule_day_night()

    async def sensor_enable(self, packet: knxdclient.ReceivedGroupAPDU):
        """
        Enable or Disable Automatic Shading
        """

        print("Received group telegram: {}".format(packet))

        await self.connection.group_write(util.str_to_group_address(self.device_config["actors"]["position-slat"]),
                                          knxdclient.KNXDAPDUType.WRITE,
                                          knxdclient.encode_value(util.percentage_to_int(100),
                                                                  knxdclient.KNXDPT.UINT8))

        # TODO: start/stop scheduled jobs
        # TODO: enable/disable flag

        pass
