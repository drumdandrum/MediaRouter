from abc import ABC, abstractmethod


class OutputPlugin(ABC):
    """Contract implemented by future output adapters.

    The foundation defines the shape only. Concrete plugins such as STRM,
    M3U, XMLTV, and HDHomeRun should be added in their own module folders
    once the catalog and settings contracts are stable.
    """

    name: str
    label: str
    description: str

    @abstractmethod
    def status(self) -> dict:
        raise NotImplementedError
