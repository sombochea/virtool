import asyncio
from typing import Tuple

import aiohttp.web
import aiojobs.aiohttp

import virtool.http.accept
import virtool.jobs.auth
from virtool.dev.fake import drop_fake_mongo, remove_fake_data_path
from virtool.jobs.routes import init_routes
from virtool.process_utils import create_app_runner, wait_for_restart, wait_for_shutdown
from virtool.shutdown import drop_fake_postgres
from virtool.startup import init_fake_config, init_redis, init_db, init_postgres, init_settings, init_executors, \
    init_fake, init_events
from virtool.types import App


async def create_app(**config):
    """Crate the :class:`aiohttp.web.Application` for the jobs API process."""
    middlewares = [
        virtool.http.accept.middleware,
        virtool.jobs.auth.middleware,
    ]

    app: aiohttp.web.Application = aiohttp.web.Application(middlewares=middlewares)

    app["config"] = config
    app["mode"] = "jobs_api_server"

    aiojobs.aiohttp.setup(app)

    app.on_startup.extend([
        init_fake_config,
        init_redis,
        init_db,
        init_postgres,
        init_settings,
        init_executors,
        init_fake,
        init_events,
        init_routes,
    ])

    app.on_shutdown.extend([
        shutdown,
        drop_fake_mongo,
        drop_fake_postgres,
        remove_fake_data_path,
    ])

    return app


async def shutdown(app: App):
    try:
        app["redis"].close()
        await app["redis"].wait_closed()
    except KeyError:
        pass


async def start_aiohttp_server(
        host: str, port: int, **config
) -> Tuple[aiohttp.web.Application, aiohttp.web.AppRunner]:
    """
    Create the :class:`aiohttp.web.Application` and start the aiohttp server
    for the jobs API process.
    """
    app = await create_app(**config)
    runner = await create_app_runner(app, host, port)

    return app, runner


async def run(**config):
    """
    Run the jobs API server.

    :param dev: If True, the log level will be set to DEBUG
    :param verbose: Same effect as :obj:`dev`
    :param config: Any other configuration options as keyword arguments
    """
    app, runner = await start_aiohttp_server(**config)

    _, pending = await asyncio.wait(
        [wait_for_restart(runner, app["events"]),
         wait_for_shutdown(runner, app["events"])],
        return_when=asyncio.FIRST_COMPLETED,
    )

    for job in pending:
        job.cancel()
