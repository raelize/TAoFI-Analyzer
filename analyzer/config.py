"""Configuration: the server side settings and what a browser tab shows (the config-store)."""
from dataclasses import dataclass, field, fields

# recolor targets, in the order of the recolor input fields
COLORS = ["green", "yellow", "magenta", "orange", "cyan", "blue", "black", "red"]


@dataclass
class AnalyzerConfig:
    """What the browser shows; kept per browser tab in the config-store."""

    directory: str = ""
    database: str | None = None
    x: str | None = None
    y: str | None = None
    z: str | None = None  # for the 3d graph
    # the second plot of scatter2
    x2: str | None = None
    y2: str | None = None
    jitter: float = 0
    argv: str = ""
    query: str = ""
    dbkey: str = ""
    graph: str = "scatter"
    fixgreen: bool = True
    # whether the text fields applied before (e.g. before a reload of the page) are restored (see update_store())
    fields_restored: bool = False
    # color -> [regex, label]
    colors: dict[str, list[str | None]] = field(default_factory=lambda: {color: [None, None] for color in COLORS})

    @classmethod
    def from_store(cls, store):
        """Config from the config-store, ignoring unknown keys (e.g. from a page of an older version)."""
        names = {f.name for f in fields(cls)}
        return cls(**{key: value for key, value in (store or {}).items() if key in names})


# Server side settings (directory and presets from the command line or the
# environment); also the initial config-store of every browser tab.
server_config = AnalyzerConfig()


# light background per color code for the rows of the data tab
ROW_COLORS = {
    "P": "#fde2ec",
    "G": "#d5f5e3",
    "Y": "#fcf3cf",
    "M": "#ebdef0",
    "O": "#fae5d3",
    "C": "#d1f2f5",
    "B": "#d4e6f1",
    "Z": "#d6dbdf",
    "R": "#fadbd8",
}
