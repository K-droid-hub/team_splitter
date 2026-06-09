import os

_raw = os.getenv("GUILD_IDS", "")
GUILD_IDS = [int(gid.strip()) for gid in _raw.split(",") if gid.strip()]
