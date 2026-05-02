"""Yellow-banner warning printer used by optional-hardware code paths.

Both ``raiden.audio`` and ``raiden.robot.footpedal`` print the same
banner shape when an optional dependency is missing or a device fails
to open.  Centralised here so the banner stays consistent.
"""

_YELLOW = "\033[1;33m"
_RESET = "\033[0m"


def warn(msg: str) -> None:
    print(f"{_YELLOW}{'!' * 60}{_RESET}")
    for line in msg.splitlines():
        print(f"{_YELLOW}  {line}{_RESET}")
    print(f"{_YELLOW}{'!' * 60}{_RESET}\n")
