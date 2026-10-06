from module.config.utils import get_os_reset_remain
from module.exception import RequestHumanTakeover
from module.logger import logger
from module.os.map import OSMap
from module.os.tasks.task_context import is_proxied


class OpsiAbyssal(OSMap):
    def delay_abyssal(self, result=True):
        """
        Args:
            result(bool): If still have obscure coordinates.
        """
        if is_proxied(self.config):
            # The scheduler owns the loop and the delay of the proxied sub tasks,
            # never delay or stop them from here.
            return
        if get_os_reset_remain() == 0:
            logger.info('Just less than 1 day to OpSi reset, delay 2.5 hours')
            self.config.task_delay(minute=150, server_update=True)
            self.config.task_stop()
        elif not result:
            self.config.task_delay(server_update=True)
            self.config.task_stop()

    def clear_abyssal(self):
        """
        Get one abyssal logger in storage,
        attack abyssal boss,
        repair fleets in port.

        Raises:
            ActionPointLimit:
            TaskEnd: If no more abyssal loggers.
            RequestHumanTakeover: If unable to clear boss, fleets exhausted.
        """
        logger.hr('OS clear abyssal', level=1)
        self.cl1_ap_preserve()

        with self.config.temporary(STORY_ALLOW_SKIP=False):
            result = self.storage_get_next_item('ABYSSAL', use_logger=self.config.OpsiGeneral_UseLogger)
        if not result:
            if is_proxied(self.config):
                # The scheduler proxies this task, report "no content" so it moves
                # on to another replenish source instead of stopping itself.
                self._coin_task_no_content = self.config.task.command
                return False
            self.delay_abyssal(result=False)

        self.config.override(
            OpsiGeneral_DoRandomMapEvent=False,
            HOMO_EDGE_DETECT=False,
            STORY_OPTION=0
        )
        self.zone_init()
        self.fleet_set(self.config.OpsiAbyssal_Fleet or self.config.OpsiFleet_Fleet)
        result = self.run_abyssal()
        if not result:
            raise RequestHumanTakeover

        self.fleet_repair(revert=False)
        self.delay_abyssal()

    def os_abyssal(self):
        while True:
            self.clear_abyssal()
            # If CL1 dispatched this to replenish yellow coins, yield back to CL1
            # once the target is reached (round boundary, never mid-round).
            if self.yellow_coins_replenish_finished():
                self.finish_yellow_coins_replenish()
            self.config.check_task_switch()
