"""ES-DE library mode: the RomM library as RetroDECK's ES-DE lists it.

Consumed via package import: the stand-in service the library sync hands each
unit to, and that downloads, uninstalls and adoption ask about one game.
"""

from services.esde.stand_ins import EsdeStandInService, EsdeStandInServiceConfig

__all__ = ["EsdeStandInService", "EsdeStandInServiceConfig"]
