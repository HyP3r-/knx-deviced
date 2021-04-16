import asyncio
import math
import time
from datetime import datetime, timedelta
from enum import Enum
from typing import List, Optional, Dict

import knxdclient
from apscheduler.job import Job
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from astral import LocationInfo
from astral.sun import sun, elevation, azimuth

import device
import util


class AutomaticShadingState(Enum):
    NIGHT = 0
    DAY = 1
    WAITING_FOR_TIME = 2
    WAITING_FOR_SUN = 3
    SHADING_READY = 4
    SHADING = 5


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

        # automatic shading configuration
        self.automatic_shading_state = AutomaticShadingState.NIGHT
        self.automatic_shading_sun_on = util.SwitchOnOffDelay(timedelta(minutes=5))
        self.automatic_shading_sun_off = util.SwitchOnOffDelay(timedelta(minutes=20))
        self.automatic_shading_sun_active = False
        self.automatic_shading_wait_time = None
        self.automatic_shading_slat_current = None

        # sun configuration
        self.location_info = LocationInfo(self.core_config["location"]["name"],
                                          self.core_config["location"]["region"],
                                          self.core_config["location"]["timezone"],
                                          self.core_config["location"]["latitude"],
                                          self.core_config["location"]["longitude"])

        # jobs
        self.scheduler_jobs = []

        # sensors
        self.enabled = False
        self.outdoor_brightness = 0.0
        self.setpoint_brightness = 0.0

    async def state_save(self) -> dict:
        return {
            "enabled": self.enabled,
            "outdoor_brightness": self.outdoor_brightness,
            "setpoint_brightness": self.setpoint_brightness,
            "automatic_shading_state": self.automatic_shading_state,
            "automatic_shading_sun_on": self.automatic_shading_sun_on,
            "automatic_shading_sun_off": self.automatic_shading_sun_off,
            "automatic_shading_sun_active": self.automatic_shading_sun_active,
            "automatic_shading_wait_time": self.automatic_shading_wait_time,
            "automatic_shading_slat_current": self.automatic_shading_slat_current,
        }

    async def state_load(self, state: dict):
        self.enabled = state["enabled"]
        self.outdoor_brightness = state["outdoor_brightness"]
        self.setpoint_brightness = state["setpoint_brightness"]

        self.automatic_shading_state = state["automatic_shading_state"]
        self.automatic_shading_sun_on = state["automatic_shading_sun_on"]
        self.automatic_shading_sun_off = state["automatic_shading_sun_off"]
        self.automatic_shading_sun_active = state["automatic_shading_sun_active"]
        self.automatic_shading_wait_time = state["automatic_shading_wait_time"]
        self.automatic_shading_slat_current = state["automatic_shading_slat_current"]

    async def init(self):
        """
        Init System
        """

        await self.schedule_day_night()
        await self.schedule_automatic_shading()

    def get_sun(self, date: Optional[datetime.date] = None) -> Dict:
        """
        Return the sun with the current location configuration
        """

        return sun(self.location_info.observer, tzinfo=self.location_info.tzinfo, date=date)

    async def schedule_day_night(self):
        """
        Schedule next sunrise or sunset job
        """

        if not self.enabled:
            return

        # get dusk and dawn of today
        s = self.get_sun()
        dusk, dawn = s["dusk"], s["dawn"]

        # if we start after dawn we have to fetch tomorrow
        now = datetime.now(tz=self.location_info.tzinfo)
        if now > dawn:
            s = self.get_sun(date=now + timedelta(days=1))
            dusk, dawn = s["dusk"], s["dawn"]

        day_night = AutomaticShadingDayNight.NIGHT if dawn < now else AutomaticShadingDayNight.DAY
        target_date = dusk if dawn < now else dawn

        self.automatic_shading_state = \
            AutomaticShadingState.NIGHT if day_night == AutomaticShadingDayNight.NIGHT else AutomaticShadingState.DAY

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

        self.automatic_shading_state = \
            AutomaticShadingState.NIGHT if day_night == AutomaticShadingDayNight.NIGHT else AutomaticShadingState.DAY

        await self.actors_send(position_height, position_slat)

        await self.schedule_day_night()

    async def schedule_automatic_shading(self):
        """
        Schedule automatic shading
        """

        if not self.enabled:
            return

        job = self.scheduler.add_job(self.automatic_shading, "cron", second=0, minute="*")
        self.scheduler_jobs.append(job)

    async def automatic_shading(self):
        """
        Process every minute the automatic shading
        """

        if not self.enabled:
            return

        # wait for brightness to come and end
        if not self.automatic_shading_sun_active and self.outdoor_brightness > self.setpoint_brightness:
            result = self.automatic_shading_sun_on.process()
            if result:
                self.logger.info(f"Sun is now active {self.outdoor_brightness}, {self.setpoint_brightness}")
                self.automatic_shading_sun_active = True
        else:
            self.automatic_shading_sun_on.reset()

        if self.automatic_shading_sun_active and self.outdoor_brightness < self.setpoint_brightness:
            result = self.automatic_shading_sun_off.process()
            if result:
                self.logger.info(f"Sun is now inactive {self.outdoor_brightness}, {self.setpoint_brightness}")
                self.automatic_shading_sun_active = False
        else:
            self.automatic_shading_sun_off.reset()

        # wait for sun in range
        _elevation = elevation(self.location_info.observer)
        _azimuth = azimuth(self.location_info.observer)

        device_config_parameter = self.device_config["parameter"]
        cardinal_direction_start = device_config_parameter["cardinal_direction"] - \
                                   device_config_parameter["cardinal_direction_start"]
        cardinal_direction_stop = device_config_parameter["cardinal_direction"] + \
                                  device_config_parameter["cardinal_direction_stop"]

        # wait for the sun to come in range
        if self.automatic_shading_state == AutomaticShadingState.DAY and \
            cardinal_direction_start <= _azimuth <= cardinal_direction_stop:
            self.logger.info(f"Waiting now for Start")
            self.automatic_shading_state = AutomaticShadingState.WAITING_FOR_TIME
            self.automatic_shading_wait_time = time.time()

        # wait for the switch on delay
        if self.automatic_shading_state == AutomaticShadingState.WAITING_FOR_TIME and \
            self.automatic_shading_wait_time + device_config_parameter["automatic_shading_start_delay"] < time.time():
            self.logger.info(f"Shading Ready")
            self.automatic_shading_state = AutomaticShadingState.SHADING_READY

        # end the shading when the sun leaves the range
        # TODO: search for the azimuth where its the cardinal direction stop minus the shading stop delay
        if self.automatic_shading_state == AutomaticShadingState.SHADING_READY and _azimuth >= cardinal_direction_stop:
            self.logger.info(f"Shading now ended")
            self.automatic_shading_state = AutomaticShadingState.DAY

        # when it is bright enough and the sun is in range we can start shading
        if self.automatic_shading_state == AutomaticShadingState.SHADING_READY and self.automatic_shading_sun_active:
            self.logger.info(f"Start shading")
            self.automatic_shading_state = AutomaticShadingState.SHADING

        # when it gets darker again, stop the automatic shading and raise the shades
        if self.automatic_shading_state == AutomaticShadingState.SHADING and not self.automatic_shading_sun_active:
            self.logger.info(f"Stop shading, its too dark")
            await self.actors_send(0, 0)
            self.automatic_shading_slat_current = 0
            self.automatic_shading_state = AutomaticShadingState.SHADING_READY

        # do the automatic shading
        if self.automatic_shading_state == AutomaticShadingState.SHADING:
            # get configuration
            distance_slats = device_config_parameter["distance_slats"]
            wide_slats = device_config_parameter["wide_slats"]
            minimum_change_tracking = device_config_parameter["minimum_change_tracking"]

            # calculate the angle
            alpha = ((math.pi / 2) - math.radians(_elevation))
            gamma = alpha + math.asin((distance_slats / 2.0) * (math.sin(alpha) / (wide_slats / 2.0)))
            _gamma = max(min(gamma, math.pi), 0.0)

            position_slat = (100.0 / math.pi) * _gamma

            if self.automatic_shading_slat_current is None or \
                position_slat < self.automatic_shading_slat_current - minimum_change_tracking or \
                position_slat > self.automatic_shading_slat_current + minimum_change_tracking:
                self.logger.info(f"Shading send new value {100} {position_slat}")
                await self.actors_send(100, position_slat)
                self.automatic_shading_slat_current = position_slat

    async def actors_send(self, position_height, position_slat):
        """
        Send height and slat position to actor
        """

        self.logger.info(f"Send new values {position_height} {position_slat}")
        device_config_actors = self.device_config["actors"]
        await self.connection.group_write(
            util.str_to_group_address(device_config_actors["position_height"]),
            knxdclient.KNXDAPDUType.WRITE,
            knxdclient.encode_value(util.percentage_to_int(position_height), knxdclient.KNXDPT.UINT8))
        await self.connection.group_write(
            util.str_to_group_address(device_config_actors["position_slat"]),
            knxdclient.KNXDAPDUType.WRITE,
            knxdclient.encode_value(util.percentage_to_int(position_slat), knxdclient.KNXDPT.UINT8))

    async def sensor_enable(self, packet: knxdclient.ReceivedGroupAPDU):
        """
        Enable or Disable Automatic Shading
        """

        enabled = knxdclient.decode_value(packet.payload.value, knxdclient.KNXDPT.BOOLEAN)
        self.logger.info(f"Received Sensor Enable {enabled}")

        if enabled == self.enabled:
            return

        self.enabled = enabled

        if self.enabled:
            await self.init()
        else:
            for job in self.scheduler_jobs:
                self.scheduler.remove_job(job.id)

    async def sensor_outdoor_brightness(self, packet: knxdclient.ReceivedGroupAPDU):
        if not util.packet_with_payload(packet):
            return
        self.outdoor_brightness = knxdclient.decode_value(packet.payload.value, knxdclient.KNXDPT.FLOAT16)
        self.logger.info(f"Received Sensor Outdoor Brightness {self.outdoor_brightness}")
        await self.automatic_shading()

    async def sensor_setpoint_brightness(self, packet: knxdclient.ReceivedGroupAPDU):
        if not util.packet_with_payload(packet):
            return
        self.setpoint_brightness = knxdclient.decode_value(packet.payload.value, knxdclient.KNXDPT.FLOAT16)
        self.logger.info(f"Received Sensor Setpoint Brightness {self.setpoint_brightness}")
        await self.automatic_shading()

    async def sensor_switch_on_delay(self, packet: knxdclient.ReceivedGroupAPDU):
        if not util.packet_with_payload(packet):
            return
        delay = knxdclient.decode_value(packet.payload.value, knxdclient.KNXDPT.UINT16)
        self.logger.info(f"Received Sensor Switch On Delay {delay}")
        self.automatic_shading_sun_on.set_delay(delay)
        await self.automatic_shading()

    async def sensor_switch_off_delay(self, packet: knxdclient.ReceivedGroupAPDU):
        if not util.packet_with_payload(packet):
            return
        delay = knxdclient.decode_value(packet.payload.value, knxdclient.KNXDPT.UINT16)
        self.logger.info(f"Received Sensor Switch Off Delay {delay}")
        self.automatic_shading_sun_off.set_delay(delay)
        await self.automatic_shading()
