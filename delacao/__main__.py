import threading

import uvicorn

from . import collector, config
from .web import app


def main():
    threading.Thread(target=collector.run, daemon=True, name="collector").start()
    print(f"Delação Premiada: coletor ativo; Revisão em http://{config.HOST}:{config.PORT}")
    uvicorn.run(app, host=config.HOST, port=config.PORT, log_level="warning")


if __name__ == "__main__":
    main()
