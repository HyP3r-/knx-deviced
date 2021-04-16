import asyncio
import logging
from abc import abstractmethod

import knxdclient
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import util


class Device:
    def __init__(self, connection: knxdclient.KNXDConnection, loop: asyncio.AbstractEventLoop,
                 scheduler: AsyncIOScheduler, device_config: dict, core_config: dict, name: str):
        self.connection = connection
        self.loop = loop
        self.scheduler = scheduler
        self.device_config = device_config
        self.core_config = core_config
        self.name = name
        self.logger = logging.getLogger(util.sane_logger_name(f"knx-deviced.{self.device_config['general']['name']}"))

    @abstractmethod
    async def state_load(self, state: dict):
        pass

    @abstractmethod
    async def state_save(self) -> dict:
        pass

    @abstractmethod
    async def init(self):
        pass
