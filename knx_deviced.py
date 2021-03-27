#!/usr/bin/python3

import asyncio
import importlib
import logging
import os
import pickle
import signal
import sys
from typing import List, Dict, Callable

import knxdclient
import toml
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import device

__author__ = "Andreas Fendt"
__copyright__ = "Copyright 2021, Andreas Fendt"
__credits__ = ["Andreas Fendt"]
__maintainer__ = "Andreas Fendt"
__email__ = "info@fendt-it.com"
__status__ = "Production"

# configure logging
logger = logging.getLogger("knx-deviced")
logger.setLevel(logging.INFO)
handler_stream = logging.StreamHandler(sys.stdout)
formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(process)d | %(message)s")
handler_stream.setFormatter(formatter)
logger.addHandler(handler_stream)


def handle_unhandled_exception(exc_type, exc_value, exc_traceback):
    """
    Handler for unhandled exceptions that will write to the logs
    """

    if issubclass(exc_type, KeyboardInterrupt):
        # call the default excepthook saved at __excepthook__
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return

    logger.critical("Unhandled exception", exc_info=(exc_type, exc_value, exc_traceback))


sys.excepthook = handle_unhandled_exception


class DeviceInstance:
    def __init__(self, name: str, config: dict, instance: device.Device):
        self.name = name
        self.config = config
        self.instance = instance


class Core:
    device_instances: List[DeviceInstance]
    device_handlers: Dict[str, List[Callable]]

    def __init__(self):
        # global variables
        self.device_instances = []
        self.device_handlers = {}
        self.project_path = os.path.dirname(__file__)

        # create knx-connection, async loop and scheduler
        self.connection = knxdclient.KNXDConnection()
        self.loop = asyncio.get_event_loop()
        self.scheduler = AsyncIOScheduler()

        # import devices modules
        self.device_modules = []
        files = os.listdir(os.path.join(self.project_path, "devices"))
        for file in files:
            filename, file_extension = os.path.splitext(file)
            if file_extension != ".py":
                continue
            self.device_modules.append(importlib.import_module(f"devices.{filename}"))

        # bind signals
        for signal_name in ("SIGINT", "SIGTERM"):
            self.loop.add_signal_handler(getattr(signal, signal_name), lambda: asyncio.ensure_future(self.stop()))

        # load configuration
        with open(os.path.join(self.project_path, "config", "core", "config.toml"), "r") as f:
            self.core_config = toml.load(f)

    def run(self):
        """
        Startup System by creating for each configuration file a instance
        """

        self.loop.run_until_complete(self._run())

    async def _run(self):
        """
        Register Telegram Handler, connect to knxd and run in background
        """

        await self.devices_create()
        await self.devices_init()

        self.scheduler.start()

        self.connection.register_telegram_handler(self.handler)
        await self.connection.connect()
        run_task = asyncio.create_task(self.connection.run())
        await self.connection.open_group_socket()
        await run_task

    async def devices_create(self):
        """
        Scan folder config/devices for configuration files and create instance
        """

        path_config_devices = os.path.join(self.project_path, "config", "devices")
        files = os.listdir(path_config_devices)

        for file in files:
            filename, file_extension = os.path.splitext(file)
            if file_extension != ".toml":
                continue

            # load configuration
            with open(os.path.join(path_config_devices, file), "r") as f:
                device_config = toml.load(f)

            # search for module
            device_cls_str = device_config["general"]["class"]
            device_cls = None
            for device_module in self.device_modules:
                if device_cls_str in dir(device_module):
                    device_cls = getattr(device_module, device_cls_str)
                    break

            if device_cls is None:
                logger.warning(f"No Class found for {file}")
                continue

            # create instance
            instance = device_cls(self.connection, self.loop, self.scheduler, device_config, self.core_config)
            device_instance = DeviceInstance(filename, device_config, instance)
            self.device_instances.append(device_instance)

            # bind handlers
            for sensor, group_address in dict(device_config["sensors"]).items():
                if group_address not in self.device_handlers:
                    self.device_handlers[group_address] = []
                func = getattr(instance, f"sensor_{sensor}")
                self.device_handlers[group_address].append(func)

    async def devices_init(self):
        """
        Load state from the persistence folder and init instances
        """

        for device_instance in self.device_instances:
            path = os.path.join(self.project_path, "persistence", f"{device_instance.name}.pickle")

            if not os.path.exists(path):
                continue

            try:
                with open(path, "rb") as f:
                    state = pickle.load(f)
            except:
                logger.exception("Error while loading saved state")
                continue

            await device_instance.instance.state_load(state)

        for device_instance in self.device_instances:
            await device_instance.instance.init()

    async def handler(self, packet: knxdclient.ReceivedGroupAPDU) -> None:
        """
        Handler for incoming packets
        """

        group_addr = str(packet.dst)

        if group_addr not in self.device_handlers:
            return

        for func in self.device_handlers[group_addr]:
            await func(packet)

    async def stop(self):
        """
        Save the state of the devices and shutdown
        """

        # save the state
        for device_instance in self.device_instances:
            path = os.path.join(self.project_path, "persistence", f"{device_instance.name}.pickle")
            state = await device_instance.instance.state_save()
            with open(path, "wb") as f:
                pickle.dump(state, f)

        # stop connection
        await self.connection.stop()


if __name__ == "__main__":
    core = Core()
    core.run()
