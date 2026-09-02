from datetime import datetime, timedelta

from module.base.timer import Timer
from module.exception import RequestHumanTakeover
from module.logger import logger


class EmulatorRestart:
    """
    Restart the emulator (and ADB) on a timer, to relieve adb glitches and
    emulator memory leaks.

    The scheduler is serial, so this task only runs between rounds of other
    tasks. If the timer fires while another task is running, the current task
    yields via check_task_switch() and this task runs after that round ends,
    combat is never interrupted.

    The interval is Emulator.RestartEmulatorInterval in hours, 0 to disable.
    """
    def __init__(self, config, device):
        self.config = config
        self.device = device

    def _wait_device_ready(self, timeout=180):
        """
        Wait until the device is really usable, not merely its adb port is up.

        `MuMuManager.exe control -v {id} restart` is fire-and-forget: it returns before
        Android has actually rebooted, so emulator_start_watch() can report 'online'
        based on an adb connection that the still-running restart is about to tear down.
        The next task then dies on its first device init (AdbError: remote end closed
        connection). Taking a screenshot here forces the same u2/atx-agent connection
        that the next task would set up, and retries until it holds.

        Returns:
            bool: True if the device became usable within the timeout, else False.
        """
        logger.hr('Device ready check', level=1)
        interval = Timer(2).start()
        timeout_timer = Timer(timeout).start()

        while 1:
            interval.wait()
            interval.reset()
            if timeout_timer.reached():
                logger.warning(f'Device not usable {timeout}s after emulator restart')
                return False

            try:
                self.device.screenshot()
                logger.info('Device is ready after emulator restart')
                return True
            except Exception as e:
                # The restart may still be tearing down the old adb/atx-agent session,
                # so the connection is not stable yet. Keep probing.
                logger.info(f'Device not ready yet after emulator restart: {e}')

    def run(self):
        interval = self.config.EmulatorRestart_RestartEmulatorInterval
        if interval <= 0:
            logger.info('RestartEmulatorInterval=0, emulator restart is disabled')
            self.config.task_delay(server_update=True)
            return

        logger.hr('Emulator restart', level=1)
        # Shut down the emulator. For MuMu12 this is MuMuManager.exe control shutdown,
        # it does not need adb, so it works even if adb itself is broken.
        if not self.device.emulator_stop():
            logger.error('Failed to stop emulator, retry next interval')
            self.config.task_delay(target=datetime.now() + timedelta(hours=interval))
            return

        # Start the emulator again. emulator_start() internally retries 3 times and
        # waits (emulator_start_watch, 180s) for the device to come back online.
        if not self.device.emulator_start():
            # The emulator is stopped but cannot be started again, ALAS cannot continue.
            logger.critical('Failed to start emulator after stop, request human takeover')
            raise RequestHumanTakeover

        # emulator_start_watch() can pass on an adb port that the async `control restart`
        # is about to kill. Confirm the device is actually usable before yielding, or the
        # next task fails on its first screenshot.
        if not self._wait_device_ready():
            logger.critical('Emulator restarted but device is not usable, request human takeover')
            raise RequestHumanTakeover

        logger.info(f'Emulator restarted, delay next restart by {interval} hours')
        self.config.task_delay(target=datetime.now() + timedelta(hours=interval))
