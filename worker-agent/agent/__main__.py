from __future__ import annotations

import uvicorn

from .config import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "agent.app:app",
        host=settings.worker_host,
        port=settings.worker_port,
        reload=False,
    )


if __name__ == "__main__":
    main()
