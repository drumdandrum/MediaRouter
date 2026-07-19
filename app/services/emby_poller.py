import asyncio

from app.services.emby import EmbyError, get_emby_settings, poll_emby_once, record_poll_failure
from app.services.logs import add_log


class EmbyPoller:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._poll_lock = asyncio.Lock()

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self.running:
            return
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(self._run(), name="emby-session-poller")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            try:
                await self._task
            finally:
                self._task = None

    async def poll_once(self) -> bool:
        if self._poll_lock.locked():
            add_log("warning", "emby", "poll_skipped reason=overlap")
            return False
        async with self._poll_lock:
            try:
                await asyncio.to_thread(poll_emby_once)
            except Exception as exc:
                record_poll_failure(EmbyError("The Emby polling cycle failed.", "error"))
                add_log("error", "emby", f"poll_failed_unexpectedly error={type(exc).__name__}")
            return True

    async def _run(self) -> None:
        try:
            while not self._stop.is_set():
                settings = get_emby_settings()
                if settings.enabled and settings.server_url and settings.has_api_key:
                    await self.poll_once()
                interval = max(settings.poll_interval_seconds, 5)
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=interval)
                except TimeoutError:
                    pass
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            add_log("error", "emby", f"poller_stopped_unexpectedly error={type(exc).__name__}")
