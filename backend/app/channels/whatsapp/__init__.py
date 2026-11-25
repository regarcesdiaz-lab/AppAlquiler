"""WhatsApp channel (central number). MESSAGING_MODE picks the implementation:

* ``simulator`` (default, the only option outside production): SimulatorChannel, messages stay
  in local Redis and show up in the simulator page.
* ``live`` (production only): LiveWhatsAppChannel, Meta WhatsApp Cloud API.
"""

from app.channels.whatsapp.base import WhatsAppChannel
from app.channels.whatsapp.live import LiveWhatsAppChannel
from app.channels.whatsapp.simulator import SimulatorChannel
from app.core.config import get_settings
from app.jobs.queue import get_redis

_override: WhatsAppChannel | None = None
_live: LiveWhatsAppChannel | None = None  # one HTTP connection pool per process
_simulator: SimulatorChannel | None = None


def set_whatsapp_channel(channel: WhatsAppChannel | None) -> None:
    """Tests replace the channel; None restores the configured one."""
    global _override
    _override = channel


def get_whatsapp_channel() -> WhatsAppChannel:
    if _override is not None:
        return _override
    settings = get_settings()
    if settings.MESSAGING_MODE == "live":
        global _live
        if _live is None:
            _live = LiveWhatsAppChannel(settings)
        return _live
    global _simulator
    if _simulator is None:
        _simulator = SimulatorChannel(get_redis())
    return _simulator


def get_simulator() -> SimulatorChannel:
    channel = get_whatsapp_channel()
    if not isinstance(channel, SimulatorChannel):
        raise RuntimeError("The WhatsApp simulator is not the active channel")
    return channel
