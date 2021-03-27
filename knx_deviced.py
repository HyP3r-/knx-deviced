#!/usr/bin/python3

import asyncio
import importlib
import os
import pickle
import signal
from typing import List

import knxdclient
import toml
from apscheduler.schedulers.background import BackgroundScheduler

import device


class DeviceInstance:
    def __init__(self, name: str, cfg: dict, instance: device.Device):
        self.name = name
        self.cfg = cfg
        self.instance = instance


class Core:
    device_instances: List[DeviceInstance]

    def __init__(self):
        # global variables
        self.device_instances = []
        self.project_path = os.path.dirname(__file__)

        # create knx-connection, async loop and scheduler
        self.connection = knxdclient.KNXDConnection()
        self.loop = asyncio.get_event_loop()
        self.scheduler = BackgroundScheduler()

        # import devices modules
        self.device_modules = []
        files = os.listdir(os.path.join(self.project_path, "devices"))
        for file in files:
            filename, file_extension = os.path.splitext(file)
            if file_extension != ".py":
                continue
            self.device_modules.append(importlib.import_module(f"devices.{filename}"))

        # bind signals
        signal.signal(signal.SIGINT, self.stop)
        signal.signal(signal.SIGTERM, self.stop)

    def run(self):
        """
        Startup System by creating for each configuration file a instance
        """

        self.devices_create()
        self.devices_init()

        self.loop.run_until_complete(self._run())

    def devices_create(self):
        """
        Scan folder cfg/devices for configuration files and create instance
        """

        path_cfg_devices = os.path.join(self.project_path, "cfg", "devices")
        files = os.listdir(path_cfg_devices)

        for file in files:
            filename, file_extension = os.path.splitext(file)
            if file_extension != ".toml":
                continue

            with open(os.path.join(path_cfg_devices, file), "r") as f:
                cfg = toml.load(f)

            device_cls_str = cfg["general"]["class"]
            device_cls = None
            for device_module in self.device_modules:
                if device_cls_str in dir(device_module):
                    device_cls = getattr(device_module, device_cls_str)
                    break

            if device_cls is None:
                print("No Class found")
                continue

            instance = device_cls(self.connection, self.loop, self.scheduler)
            device_instance = DeviceInstance(filename, cfg, instance)
            self.device_instances.append(device_instance)

    def devices_init(self):
        """
        Load state from the persistence folder and init instances
        """

        for device_instance in self.device_instances:
            path = os.path.join(self.project_path, "persistence", f"{device_instance.name}.pickle")

            if not os.path.exists(path):
                continue

            with open(path, "r") as f:
                state = pickle.load(f)
            device_instance.instance.state_load(state)

        for device_instance in self.device_instances:
            device_instance.instance.init()

    async def _run(self):
        """
        TODO comment?
        """

        self.connection.register_telegram_handler(self.handler)

        await self.connection.connect()
        # Connection was successful. Start receive loop:
        run_task = asyncio.create_task(self.connection.run())
        # Now that the receive loop is running, we can open the KNXd Group Socket:
        await self.connection.open_group_socket()

        await run_task

    async def handler(self, packet: knxdclient.ReceivedGroupAPDU) -> None:
        # TODO send message to the devices
        print("Received group telegram: {}".format(packet))

    def stop(self, signum, frame):
        """
        Save the state of the devices and shutdown
        """

        # save the state
        for device_instance in self.device_instances:
            path = os.path.join(self.project_path, "persistence", f"{device_instance.name}.pickle")
            state = device_instance.instance.state_save()
            with open(path, "w") as f:
                pickle.dump(state, f)

        # stop connection
        self.loop.run_until_complete(self.connection.stop())


if __name__ == "__main__":
    core = Core()
    core.run()
