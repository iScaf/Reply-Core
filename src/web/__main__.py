# -*- coding: utf-8 -*-
"""Web 管理后台启动入口：python -m src.web"""
import logging
import os

import uvicorn


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    host = os.getenv("WEB_ADMIN_HOST", "127.0.0.1")
    port = int(os.getenv("WEB_ADMIN_PORT", "8000"))
    uvicorn.run(
        "src.web.app:create_app",
        factory=True,
        host=host,
        port=port,
        log_config=None,
    )


if __name__ == "__main__":
    main()
