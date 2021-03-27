import re

import knxdclient


def str_to_group_address(group_address: str):
    """
    Convert String to Group Address
    """

    res = re.search(r"(?P<main>\d+)/(?P<middle>\d+)/(?P<sub>\d+)", group_address)

    if not res:
        raise Exception("Invalid Group Address")

    return knxdclient.GroupAddress(int(res.group("main")), int(res.group("middle")), int(res.group("sub")))


def percentage_to_int(value: int):
    """
    Scale values from 0-100 to 0-255
    """

    return round((255.0 / 100.0) * float(value))
