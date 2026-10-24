"""Where paperdb keeps its two settings.

The Notion token is read from the environment, never written to the config file:
the file records which folder and which database, both of which are harmless to
leak, and nothing that grants access.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

CONFIG_PATH = Path(
    os.environ.get("PAPERDB_CONFIG")
    or Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    / "paperdb"
    / "config.toml"
)
DEFAULT_PAPERS_DIR = Path.home() / "Papers"
# Inside the papers folder: filed papers in one, unprocessed ones in the other.
LIBRARY_NAME = "db"
INBOX_NAME = "Inbox"
TOKEN_ENV = "NOTION_TOKEN"


class ConfigError(RuntimeError):
    """Raised when paperdb has not been set up, or is set up wrongly."""


@dataclass
class Config:
    papers_dir: Path
    database_id: str = ""
    email: str = ""

    @property
    def library_dir(self) -> Path:
        """Where filed papers live, each named by its citekey."""
        return self.papers_dir / LIBRARY_NAME

    @property
    def inbox_dir(self) -> Path:
        """Where PDFs wait until sync can file them."""
        return self.papers_dir / INBOX_NAME

    @property
    def bib_path(self) -> Path:
        """Where ``paperdb bibtex`` writes by default: beside the PDFs."""
        return self.papers_dir / "library.bib"


def token() -> str:
    """The Notion integration token, from the environment."""
    value = os.environ.get(TOKEN_ENV, "").strip()
    if not value:
        raise ConfigError(
            f"{TOKEN_ENV} is not set. Create an integration at "
            "https://www.notion.so/my-integrations and export its secret."
        )
    return value


def _quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def save(config: Config, path: Path | None = None) -> Path:
    """Write the config file, creating its directory."""
    path = path or CONFIG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Written by `paperdb init`. The Notion token is NOT kept here;",
        f"# paperdb reads it from ${TOKEN_ENV}.",
        f"papers_dir = {_quote(str(config.papers_dir))}",
        f"database_id = {_quote(config.database_id)}",
    ]
    if config.email:
        lines.append(
            f"# Sent to OpenAlex and Unpaywall so requests land in the polite pool."
        )
        lines.append(f"email = {_quote(config.email)}")
    path.write_text("\n".join(lines) + "\n")
    return path


def load(path: Path | None = None) -> Config:
    """Read the config file, or explain how to create it."""
    path = path or CONFIG_PATH
    if not path.exists():
        raise ConfigError(f"no config at {path}. Run `paperdb init` first.")
    data = tomllib.loads(path.read_text())
    papers_dir = data.get("papers_dir")
    if not papers_dir:
        raise ConfigError(f"{path} has no papers_dir. Run `paperdb init` again.")
    return Config(
        papers_dir=Path(papers_dir).expanduser(),
        database_id=data.get("database_id", ""),
        email=data.get("email", ""),
    )
