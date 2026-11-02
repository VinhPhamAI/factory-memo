"""Replace Windows reload workers without unreliable console Ctrl+C events."""

from uvicorn._subprocess import get_subprocess
from uvicorn.supervisors.watchfilesreload import WatchFilesReload


class WindowsReload(WatchFilesReload):
    """Use bounded process termination with the locked Uvicorn file watcher.

    Windows console Ctrl+C events addressed to a nonzero process group can
    succeed without delivering a signal. Reload is allowed to interrupt work.
    The private Uvicorn subprocess helper is covered by the committed lock and
    actual editable-install reload tests.
    """

    def __init__(self, config, target, sockets):
        """Initialize the file watcher and track its replaceable worker.

        Args:
            config: Uvicorn settings shared by every reload worker.
            target: Server run method called in each child process.
            sockets: Listening sockets retained by the supervising process.
        """
        super().__init__(config=config, target=target, sockets=sockets)
        self.process = None

    def _stop_worker(self) -> None:
        """Stop the old worker within bounded waits before replacement.

        Raises:
            RuntimeError: The worker remains alive after terminate and kill.
        """
        if getattr(self, "process", None) is None:
            return
        if self.process.is_alive():
            self.process.terminate()
            self.process.join(timeout=5)
        if self.process.is_alive():
            self.process.kill()
            self.process.join(timeout=5)
        if self.process.is_alive():
            raise RuntimeError("The previous reload worker could not be stopped.")
        self.process.close()
        self.process = None

    def restart(self) -> None:
        """Stop the previous worker before starting its replacement.

        Raises:
            RuntimeError: The previous worker cannot be stopped safely.
        """
        self._stop_worker()
        self.process = get_subprocess(
            config=self.config, target=self.target, sockets=self.sockets
        )
        self.process.start()

    def shutdown(self) -> None:
        """Stop the worker and release listening sockets even after stop failure.

        Raises:
            RuntimeError: The worker remains alive after both bounded stop attempts.
        """
        self.should_exit.set()
        try:
            self._stop_worker()
        finally:
            for sock in self.sockets:
                sock.close()

    def run(self) -> None:
        """Watch source changes and always clean up after a restart failure.

        Raises:
            RuntimeError: A previous worker cannot be stopped for replacement.
        """
        try:
            self.startup()
            for changes in self:
                if changes:
                    self.restart()
        finally:
            self.shutdown()
