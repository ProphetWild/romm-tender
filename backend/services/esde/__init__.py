"""ES-DE library mode: the RomM library as RetroDECK's ES-DE lists it.

Consumed via package import: the pass the library sync hands each unit to, the
stand-in service downloads, uninstalls and adoption ask about one game, the
media service ES-DE's selection of a game reaches, the gamelist service ES-DE's
start reaches, the hook service ES-DE's event scripts call, the service
that writes those scripts, and the one that answers the settings section.
"""

from services.esde.gamelists import EsdeGamelistService, EsdeGamelistServiceConfig
from services.esde.hook_scripts import EsdeHookScriptService, EsdeHookScriptServiceConfig
from services.esde.hooks import EsdeHookService, EsdeHookServiceConfig
from services.esde.library_pass import EsdeLibraryPass, EsdeLibraryPassConfig
from services.esde.media import EsdeMediaService, EsdeMediaServiceConfig
from services.esde.stand_ins import EsdeStandInService, EsdeStandInServiceConfig
from services.esde.status import EsdeStatusService, EsdeStatusServiceConfig

__all__ = [
    "EsdeGamelistService",
    "EsdeGamelistServiceConfig",
    "EsdeHookScriptService",
    "EsdeHookScriptServiceConfig",
    "EsdeHookService",
    "EsdeHookServiceConfig",
    "EsdeLibraryPass",
    "EsdeLibraryPassConfig",
    "EsdeMediaService",
    "EsdeMediaServiceConfig",
    "EsdeStandInService",
    "EsdeStandInServiceConfig",
    "EsdeStatusService",
    "EsdeStatusServiceConfig",
]
