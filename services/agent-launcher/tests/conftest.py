"""Pytest bootstrap for agent-launcher tests."""

from __future__ import annotations

import asyncio
import inspect
import sys
from pathlib import Path


TESTS_ROOT = Path(__file__).resolve().parent
SERVICE_ROOT = TESTS_ROOT.parent

for candidate in (str(TESTS_ROOT), str(SERVICE_ROOT)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)


def pytest_configure(config) -> None:
    config.addinivalue_line("markers", "asyncio: run coroutine test in a local event loop")


def pytest_pyfunc_call(pyfuncitem):
    if "asyncio" not in pyfuncitem.keywords:
        return None
    testfunction = pyfuncitem.obj
    if not inspect.iscoroutinefunction(testfunction):
        return None
    signature = inspect.signature(testfunction)
    supported_kwargs = {
        name: value
        for name, value in pyfuncitem.funcargs.items()
        if name in signature.parameters
    }
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        loop.run_until_complete(testfunction(**supported_kwargs))
    finally:
        pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
        for task in pending:
            task.cancel()
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        asyncio.set_event_loop(None)
        loop.close()
    return True
