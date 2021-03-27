import asyncio
from datetime import datetime, timedelta
from enum import Enum
from typing import List

import knxdclient
from apscheduler.job import Job
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
    enabled: bool
    automatic_shading_state: AutomaticShadingState
    scheduler_jobs: List[Job]

    def __init__(self, connection: knxdclient.KNXDConnection, loop: asyncio.AbstractEventLoop,
                 scheduler: AsyncIOScheduler, device_config: dict, core_config: dict, name: str):
        super().__init__(connection, loop, scheduler, device_config, core_config, name)
        self.enabled = False
        self.automatic_shading_state = AutomaticShadingState.NIGHT
        self.location_info = LocationInfo(self.core_config["location"]["name"],
                                          self.core_config["location"]["region"],
                                          self.core_config["location"]["timezone"],
                                          self.core_config["location"]["latitude"],
                                          self.core_config["location"]["longitude"])
        self.scheduler_jobs = []
        self.outdoor_brightness = None
        self.switch_on_delay = 5
        self.switch_off_delay = 20

    async def state_save(self) -> dict:
        return {
            "enabled": self.enabled,
            "switch_on_delay": self.switch_on_delay,
            "switch_off_delay": self.switch_off_delay
        }

    async def state_load(self, state: dict):
        self.enabled = state["enabled"]
        self.switch_on_delay = state["switch_on_delay"]
        self.switch_off_delay = state["switch_off_delay"]

    async def init(self):
        """
        Init System
        """

        await self.schedule_day_night()
        await self.schedule_automatic_shading()

    async def schedule_day_night(self):
        """
        Schedule next sunrise or sunset job
        """

        if not self.enabled:
            return

        # get dusk and dawn of today
        s = sun(self.location_info.observer, tzinfo=self.location_info.tzinfo)
        dusk, dawn = s["dusk"], s["dawn"]

        # if we start after dawn we have to fetch tomorrow
        now = datetime.now(tz=self.location_info.tzinfo)
        if now > dawn:
            s = sun(self.location_info.observer, tzinfo=self.location_info.tzinfo,
                    date=now + timedelta(days=1))
            dusk, dawn = s["dusk"], s["dawn"]

        day_night = AutomaticShadingDayNight.NIGHT if dawn < now else AutomaticShadingDayNight.DAY
        target_date = dusk if dawn < now else dawn

        job = self.scheduler.add_job(self.day_night, "date", run_date=target_date, args=(day_night,))
        self.scheduler_jobs.append(job)

    async def day_night(self, day_night: AutomaticShadingDayNight):
        """
        Move shutters completely up or down when changing day/night or night/day
        """

        if not self.enabled:
            return

        if day_night == AutomaticShadingDayNight.DAY:
            position_height, position_slat = 0, 0
        else:
            position_height, position_slat = 100, 100

        await self.connection.group_write(util.str_to_group_address(self.device_config["actors"]["position_height"]),
                                          knxdclient.KNXDAPDUType.WRITE,
                                          knxdclient.encode_value(util.percentage_to_int(position_height),
                                                                  knxdclient.KNXDPT.UINT8))
        await self.connection.group_write(util.str_to_group_address(self.device_config["actors"]["position_slat"]),
                                          knxdclient.KNXDAPDUType.WRITE,
                                          knxdclient.encode_value(util.percentage_to_int(position_slat),
                                                                  knxdclient.KNXDPT.UINT8))
        await self.schedule_day_night()

    async def schedule_automatic_shading(self):
        """
        xxx
        """

        if not self.enabled:
            return

        job = self.scheduler.add_job(self.automatic_shading, "cron", second=0, minute="*")
        self.scheduler_jobs.append(job)

    async def automatic_shading(self):
        """
        xxx
        """

        if not self.enabled:
            return

    async def sensor_enable(self, packet: knxdclient.ReceivedGroupAPDU):
        """
        Enable or Disable Automatic Shading
        """

        enabled = knxdclient.decode_value(packet.payload.value, knxdclient.KNXDPT.BOOLEAN)

        if enabled == self.enabled:
            return

        self.enabled = enabled

        if self.enabled:
            await self.schedule_day_night()
        else:
            for job in self.scheduler_jobs:
                self.scheduler.remove_job(job.id)

    async def sensor_outdoor_brightness(self, packet: knxdclient.ReceivedGroupAPDU):
        self.outdoor_brightness = knxdclient.decode_value(packet.payload.value, knxdclient.KNXDPT.FLOAT16)

    async def sensor_switch_on_delay(self, packet: knxdclient.ReceivedGroupAPDU):
        self.switch_on_delay = knxdclient.decode_value(packet.payload.value, knxdclient.KNXDPT.UINT32)

    async def sensor_switch_off_delay(self, packet: knxdclient.ReceivedGroupAPDU):
        self.switch_off_delay = knxdclient.decode_value(packet.payload.value, knxdclient.KNXDPT.UINT32)
