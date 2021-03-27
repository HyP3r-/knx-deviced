import asyncio
from abc import abstractmethod

import knxdclient
from apscheduler.schedulers.background import BackgroundScheduler


class Device:
    def __init__(self, connection: knxdclient.KNXDConnection, loop: asyncio.AbstractEventLoop,
                 scheduler: BackgroundScheduler, config: dict):
        self.connection = connection
        self.loop = loop
        self.scheduler = scheduler
        self.config = config

    @abstractmethod
    def state_load(self, state: dict):
        pass

    @abstractmethod
    def state_save(self) -> dict:
        pass

    @abstractmethod
    def init(self):
        pass
