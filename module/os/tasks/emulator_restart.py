from datetime import datetime, timedelta

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

        logger.info(f'Emulator restarted, delay next restart by {interval} hours')
        self.config.task_delay(target=datetime.now() + timedelta(hours=interval))
