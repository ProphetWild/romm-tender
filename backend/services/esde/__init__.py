"""ES-DE library mode: the RomM library as RetroDECK's ES-DE lists it.

Consumed via package import: the pass the library sync hands each unit to, the
stand-in service downloads, uninstalls and adoption ask about one game, and
the media service ES-DE's selection of a game reaches.
"""

from services.esde.library_pass import EsdeLibraryPass, EsdeLibraryPassConfig
from services.esde.media import EsdeMediaService, EsdeMediaServiceConfig
from services.esde.stand_ins import EsdeStandInService, EsdeStandInServiceConfig

__all__ = [
    "EsdeLibraryPass",
    "EsdeLibraryPassConfig",
    "EsdeMediaService",
    "EsdeMediaServiceConfig",
    "EsdeStandInService",
    "EsdeStandInServiceConfig",
]
