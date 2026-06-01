import asyncio
from .server import mcp
from . import server


def main():
    try:
        mcp.run()  # stdio transport
    finally:
        if server._controller is not None:
            try:
                asyncio.run(server._controller.close())
            except Exception:
                pass


if __name__ == "__main__":
    main()
