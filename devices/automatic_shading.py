import asyncio
import math
from datetime import datetime, timedelta
from enum import Enum
from typing import List, Optional, Dict, Union

import knxdclient
from apscheduler.job import Job
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from astral import LocationInfo
from astral.sun import sun, elevation, azimuth

import device
import util


class AutomaticShadingState(Enum):
    IDLE = 0
    SHADING_READY = 1
    SHADING = 2


class DayNight(Enum):
    NIGHT = 0
    DAY = 1


def clamp(value: Union[float, int], _min: Union[float, int], _max: Union[float, int]) -> Union[float, int]:
    return max(min(value, _max), _min)


def closest(_dict, _value):
    """
    Find closest value in dict
    """

    key = min(_dict.keys(), key=lambda _key: abs(_key - _value))
    return key, _dict[key]


class AutomaticShadingRange:
    def __init__(self, device_config: dict, location_info: LocationInfo):
        self.device_config = device_config
        self.location_info = location_info
        self.stop_time = self.start_time = datetime.fromtimestamp(0, tz=self.location_info.tzinfo)

    async def generate_azimuth_datetimes(self, search: datetime, range_stop: int, hours: bool = False,
                                         minutes: bool = False) -> Dict[float, datetime]:
        """
        Generate for a given datetime and direction a dict of azimuth/datetime values
        """

        datetimes = [search + timedelta(hours=(1 if hours else 0) * index,
                                        minutes=(1 if minutes else 0) * index)
                     for index in range(range_stop)]
        return {azimuth(self.location_info.observer, _datetime): _datetime for _datetime in datetimes}

    async def cardinal_direction_get_time(self, start: datetime, cardinal_direction: float) -> datetime:
        """
        With a given start datetime, cardinal_direction and search_direction search for the datetime
        the next or last 24 hours
        This function is a bit optimized so we search in first step 24 hours and then 60 minutes
        """

        azimuth_datetimes = await self.generate_azimuth_datetimes(start, 24, hours=True, minutes=False)
        azimuths = list(azimuth_datetimes.keys())
        _azimuth, _ = closest(azimuth_datetimes, cardinal_direction)

        azimuth_index = azimuths.index(_azimuth)

        azimuth_datetimes_pre = azimuths[clamp(azimuth_index - 1, 0, len(azimuths))]
        azimuth_datetimes_closest = azimuths[azimuth_index]

        is_pre = cardinal_direction <= azimuth_datetimes_closest

        azimuth_datetimes_detail = await self.generate_azimuth_datetimes(
            azimuth_datetimes[azimuth_datetimes_pre if is_pre else azimuth_datetimes_closest],
            60, hours=False, minutes=True
        )

        _, result = closest(azimuth_datetimes_detail, cardinal_direction)
        return result

    async def _search_next(self):
        """
        Search for the next interval where sun is in range
        """

        device_config_parameter = self.device_config["parameter"]
        cardinal_direction_start = (device_config_parameter["cardinal_direction"] -
                                    device_config_parameter["cardinal_direction_start"]) % 360
        cardinal_direction_stop = (device_config_parameter["cardinal_direction"] +
                                   device_config_parameter["cardinal_direction_stop"]) % 360

        datetime_begin = (
            datetime.now(tz=self.location_info.tzinfo)
            if self.stop_time == datetime.fromtimestamp(0, tz=self.location_info.tzinfo)
            else self.stop_time + timedelta(hours=1)
        )
        stop_time = await self.cardinal_direction_get_time(datetime_begin, cardinal_direction_stop)
        start_time = await self.cardinal_direction_get_time(stop_time - timedelta(hours=24), cardinal_direction_start)
        self.start_time = start_time
        self.stop_time = stop_time

    async def search_next(self):
        """
        Search for next interval when sun is in range and its in the future or now
        """

        # we do not have to search for the next range when we still in the current range
        if datetime.now(tz=self.location_info.tzinfo) < self.stop_time:
            return

        await self._search_next()

    @property
    def start_time_with_offset(self) -> datetime:
        return (
            self.start_time +
            timedelta(seconds=self.device_config["parameter"]["automatic_shading_start_delay"])
        )

    @property
    def stop_time_with_offset(self) -> datetime:
        return (
            self.stop_time +
            timedelta(seconds=self.device_config["parameter"]["automatic_shading_stop_delay"])
        )


class AutomaticShading(device.Device):
    enabled: bool
    automatic_shading_state: AutomaticShadingState
    scheduler_jobs: List[Job]

    def __init__(self, connection: knxdclient.KNXDConnection, loop: asyncio.AbstractEventLoop,
                 scheduler: AsyncIOScheduler, device_config: dict, core_config: dict, name: str):
        super().__init__(connection, loop, scheduler, device_config, core_config, name)
        # sun configuration
        self.location_info = LocationInfo(self.core_config["location"]["name"],
                                          self.core_config["location"]["region"],
                                          self.core_config["location"]["timezone"],
                                          self.core_config["location"]["latitude"],
                                          self.core_config["location"]["longitude"])

        # automatic shading configuration
        self.automatic_shading_state = AutomaticShadingState.IDLE
        self.automatic_shading_sun_on = util.SwitchOnOffDelay(timedelta(minutes=5))
        self.automatic_shading_sun_off = util.SwitchOnOffDelay(timedelta(minutes=20))
        self.automatic_shading_sun_active = False
        self.automatic_shading_range = AutomaticShadingRange(self.device_config, self.location_info)

        # current position of the shade
        self.current_position_height = 0
        self.current_position_slat = 0
        self.current_day_night = DayNight.DAY

        # jobs
        self.scheduler_jobs = []

        # sensors
        self.enabled = False
        self.outdoor_brightness = 0.0
        self.setpoint_brightness = 20000.0

    async def state_save(self) -> dict:
        return {
            "enabled": self.enabled,
            "outdoor_brightness": self.outdoor_brightness,
            "setpoint_brightness": self.setpoint_brightness,
            "automatic_shading_state": self.automatic_shading_state,
            "automatic_shading_sun_on": self.automatic_shading_sun_on,
            "automatic_shading_sun_off": self.automatic_shading_sun_off,
            "automatic_shading_sun_active": self.automatic_shading_sun_active,
            "current_position_height": self.current_position_height,
            "current_position_slat": self.current_position_slat,
        }

    async def state_load(self, state: dict):
        self.enabled = state["enabled"]
        self.outdoor_brightness = state["outdoor_brightness"]
        self.setpoint_brightness = state["setpoint_brightness"]

        self.automatic_shading_state = state["automatic_shading_state"]
        self.automatic_shading_sun_on = state["automatic_shading_sun_on"]
        self.automatic_shading_sun_off = state["automatic_shading_sun_off"]
        self.automatic_shading_sun_active = state["automatic_shading_sun_active"]

        self.current_position_height = state["current_position_height"]
        self.current_position_slat = state["current_position_slat"]

    async def init(self):
        """
        Init System
        """

        await self.schedule_day_night()
        await self.schedule_automatic_shading()
        await self.schedule_range()

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
        _sun = self.get_sun()
        dusk, dawn = _sun["dusk"], _sun["dawn"]

        # if we start after dusk we have to fetch tomorrow
        now = datetime.now(tz=self.location_info.tzinfo)
        if dusk <= now:
            _sun = self.get_sun(date=now + timedelta(days=1))
            dusk, dawn = _sun["dusk"], _sun["dawn"]

        after_dawn = dawn <= now
        self.current_day_night = DayNight.DAY if after_dawn else DayNight.NIGHT
        next_datetime = dusk if after_dawn else dawn

        self.logger.info(f"Schedule Day/Night: {next_datetime}, {self.current_day_night}")
        job = self.scheduler.add_job(self.day_night, "date", run_date=next_datetime, misfire_grace_time=None)
        self.scheduler_jobs.append(job)

    async def day_night(self):
        """
        Move shutters completely up or down when changing day/night or night/day
        """

        if not self.enabled:
            return

        await self.schedule_day_night()

        await self.move_to_starting_position()

    async def move_to_starting_position(self):
        """
        Move to the respective starting position of the blind during the day or at night
        """

        position_height, position_slat = (0, 0) if self.current_day_night == DayNight.DAY else (100, 100)
        self.logger.info(f"Move to starting position: Height {position_height}/Slat {position_slat}")
        await self.actors_send(position_height, position_slat)

    async def schedule_automatic_shading(self):
        """
        Schedule automatic shading
        """

        if not self.enabled:
            return

        self.logger.info(f"Schedule Automatic Shading")
        job = self.scheduler.add_job(self.automatic_shading, "cron", minute="*", second=0, misfire_grace_time=None)
        self.scheduler_jobs.append(job)

    async def automatic_shading(self):
        """
        Process every minute the automatic shading
        """

        if not self.enabled:
            return

        # wait for brightness to come and end
        if not self.automatic_shading_sun_active and self.setpoint_brightness < self.outdoor_brightness:
            result = self.automatic_shading_sun_on.process()
            if result:
                self.logger.info(f"Sun is now active {self.outdoor_brightness:.2f}, {self.setpoint_brightness:.2f}")
                self.automatic_shading_sun_active = True
        else:
            self.automatic_shading_sun_on.reset()

        if self.automatic_shading_sun_active and self.outdoor_brightness < self.setpoint_brightness:
            result = self.automatic_shading_sun_off.process()
            if result:
                self.logger.info(f"Sun is now inactive {self.outdoor_brightness:.2f}, {self.setpoint_brightness:.2f}")
                self.automatic_shading_sun_active = False
        else:
            self.automatic_shading_sun_off.reset()

        sun_in_range = (
            self.automatic_shading_range.start_time_with_offset
            <= datetime.now(tz=self.location_info.tzinfo) <=
            self.automatic_shading_range.stop_time_with_offset
        )

        # wait for the sun to come in range
        if self.automatic_shading_state == AutomaticShadingState.IDLE and sun_in_range:
            self.logger.info(f"Sun has entered the range")
            self.automatic_shading_state = AutomaticShadingState.SHADING_READY

        # when sun goes out of range stop shading
        if (self.automatic_shading_state == AutomaticShadingState.SHADING or
            self.automatic_shading_state == AutomaticShadingState.SHADING_READY) and not sun_in_range:
            self.logger.info(f"Sun has left the range")
            await self.move_to_starting_position()
            self.automatic_shading_state = AutomaticShadingState.IDLE

        # when it is bright enough and the sun is in range we can start shading
        if self.automatic_shading_state == AutomaticShadingState.SHADING_READY and self.automatic_shading_sun_active:
            self.logger.info(f"Start automatic shading")
            self.automatic_shading_state = AutomaticShadingState.SHADING

        # when it gets darker again, stop the automatic shading and raise the shades
        if self.automatic_shading_state == AutomaticShadingState.SHADING and not self.automatic_shading_sun_active:
            self.logger.info(f"Stop automatic shading, it is too dark")
            await self.move_to_starting_position()
            self.automatic_shading_state = AutomaticShadingState.SHADING_READY

        # do the automatic shading
        if self.automatic_shading_state == AutomaticShadingState.SHADING:
            # get configuration
            device_config_parameter = self.device_config["parameter"]
            distance_slats = device_config_parameter["distance_slats"]
            wide_slats = device_config_parameter["wide_slats"]
            minimum_change_tracking = device_config_parameter["minimum_change_tracking"]

            # calculate the angle for the slats
            current_elevation = elevation(self.location_info.observer)
            alpha = ((math.pi / 2) - math.radians(current_elevation))
            gamma = alpha + math.asin((distance_slats / 2.0) * (math.sin(alpha) / (wide_slats / 2.0)))
            _gamma = clamp(gamma, 0.0, math.pi)

            position_slat = (100.0 / math.pi) * _gamma

            if (
                position_slat < self.current_position_slat - minimum_change_tracking or
                self.current_position_slat + minimum_change_tracking < position_slat
            ):
                self.logger.info(f"Shading new value: Height {100}/Slat {position_slat:.2f}")
                await self.actors_send(100, position_slat)
                self.current_position_slat = position_slat

    async def schedule_range(self):
        """
        Search for next possible range and schedule next search
        """

        await self.automatic_shading_range.search_next()

        self.logger.info(f"Schedule Range: {self.automatic_shading_range.stop_time}")
        job = self.scheduler.add_job(
            self.schedule_range, "date", run_date=self.automatic_shading_range.stop_time, misfire_grace_time=None
        )
        self.scheduler_jobs.append(job)

    async def actors_send(self, position_height: float, position_slat: float):
        """
        Send height and slat position to actor
        """

        self.logger.info(f"Send new values: Height {position_height:.2f}/Slat {position_slat:.2f}")
        device_config_actors = self.device_config["actors"]

        if self.current_position_height != position_height:
            await self.connection.group_write(
                util.str_to_group_address(device_config_actors["position_height"]),
                knxdclient.KNXDAPDUType.WRITE,
                knxdclient.encode_value(util.percentage_to_int(position_height), knxdclient.KNXDPT.UINT8))
            self.current_position_height = position_height

        if self.current_position_slat != position_slat:
            await self.connection.group_write(
                util.str_to_group_address(device_config_actors["position_slat"]),
                knxdclient.KNXDAPDUType.WRITE,
                knxdclient.encode_value(util.percentage_to_int(position_slat), knxdclient.KNXDPT.UINT8))
            self.current_position_slat = position_slat

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
                if self.scheduler.get_job(job.id):
                    self.scheduler.remove_job(job.id)
                self.scheduler_jobs.remove(job)

    async def sensor_outdoor_brightness(self, packet: knxdclient.ReceivedGroupAPDU):
        if not util.packet_with_payload(packet):
            return
        self.outdoor_brightness = knxdclient.decode_value(packet.payload.value, knxdclient.KNXDPT.FLOAT16)
        self.logger.info(f"Received Sensor Outdoor Brightness {self.outdoor_brightness:.2f}")
        await self.automatic_shading()

    async def sensor_setpoint_brightness(self, packet: knxdclient.ReceivedGroupAPDU):
        if not util.packet_with_payload(packet):
            return
        self.setpoint_brightness = knxdclient.decode_value(packet.payload.value, knxdclient.KNXDPT.FLOAT16)
        self.logger.info(f"Received Sensor Setpoint Brightness {self.setpoint_brightness:.2f}")
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
