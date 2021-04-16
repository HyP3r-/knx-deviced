import re
import time
from datetime import timedelta
from typing import Optional

import knxdclient
from unidecode import unidecode


def str_to_group_address(group_address: str):
    """
    Convert String to Group Address
    """

    res = re.search(r"(?P<main>\d+)/(?P<middle>\d+)/(?P<sub>\d+)", group_address)

    if not res:
        raise Exception("Invalid Group Address")

    return knxdclient.GroupAddress(int(res.group("main")), int(res.group("middle")), int(res.group("sub")))


def percentage_to_int(value):
    """
    Scale values from 0-100 to 0-255
    """

    return round((255.0 / 100.0) * float(value))


def packet_with_payload(packet: knxdclient.ReceivedGroupAPDU):
    """
    Return true if its a write or response
    """

    return packet.payload.type == knxdclient.KNXDAPDUType.WRITE or \
           packet.payload.type == knxdclient.KNXDAPDUType.RESPONSE


def sane_logger_name(name: str):
    """
    Make the logger name more uniform
    """

    return unidecode(name.replace(" ", "_").lower())


class SwitchOnOffDelay:
    """
    Auxiliary class for switch-on and switch-off delays
    """

    def __init__(self, delay: Optional[timedelta] = None):
        self.delay = delay.total_seconds() if delay is not None else None
        self.start = None

    def process(self):
        if self.start is None:
            self.start = time.time()

        return self.start + self.delay < time.time()

    def reset(self):
        self.start = None

    def set_delay(self, delay: timedelta):
        self.delay = delay
