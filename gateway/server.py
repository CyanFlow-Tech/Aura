from aura.server import app


if __name__ == "__main__":
    import uvicorn

    from aura.config import config

    uvicorn.run(
        app,
        host=config.app.host,
        port=config.app.port,
        loop=config.app.loop,
    )
