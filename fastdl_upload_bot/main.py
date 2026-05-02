from __future__ import annotations

import argparse
import logging

from .config import load_config
from .discord_bot import FastDLUploadBot


def main() -> None:
    parser = argparse.ArgumentParser(description="Sven Co-op FastDL upload bot")
    parser.add_argument(
        "--config",
        default="config.json",
        help="Path to the instance config file. Defaults to config.json.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config = load_config(args.config)
    bot = FastDLUploadBot(config)
    bot.run(config.discord.token)


if __name__ == "__main__":
    main()
