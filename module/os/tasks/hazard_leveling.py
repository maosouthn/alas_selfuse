from datetime import datetime

from module.config.utils import get_os_reset_remain
from module.logger import logger
from module.os.map import OSMap


class OpsiHazard1Leveling(OSMap):
    # Tasks to replenish yellow coins, limited tasks first and meowfficer farming as fallback
    YELLOW_COINS_REPLENISH_TASKS = ['OpsiStronghold', 'OpsiObscure', 'OpsiAbyssal', 'OpsiMeowfficerFarming']

    def _pick_yellow_coins_task(self):
        """
        Pick a task to replenish yellow coins.
        A task is skipped if it is disabled, or if it is delayed to the future (e.g. exhausted today).

        Returns:
            str or None: Task name to call, None if none is available.
        """
        if self.is_in_opsi_explore():
            logger.info('OpsiExplore is running, skip yellow coins replenish')
            return None
        for task in self.YELLOW_COINS_REPLENISH_TASKS:
            if not self.config.is_task_enabled(task):
                continue
            next_run = self.config.cross_get(keys=[task, 'Scheduler', 'NextRun'])
            if next_run is None:
                continue
            if isinstance(next_run, datetime) and next_run > datetime.now():
                logger.info(f'{task} is delayed until {next_run}, skipped for yellow coins replenish')
                continue
            return task
        return None

    def os_hazard1_leveling(self):
        logger.hr('OS hazard 1 leveling', level=1)
        # Without these enabled, CL1 gains 0 profits
        self.config.override(
            OpsiGeneral_DoRandomMapEvent=True,
            OpsiGeneral_AkashiShopFilter='ActionPoint',
        )
        if not self.config.is_task_enabled('OpsiMeowfficerFarming'):
            self.config.cross_set(keys='OpsiMeowfficerFarming.Scheduler.Enable', value=True)
        while True:
            # Limited action point preserve of hazard 1 to 200
            self.config.OS_ACTION_POINT_PRESERVE = 200
            if self.config.is_task_enabled('OpsiAshBeacon') \
                    and not self._ash_fully_collected \
                    and self.config.OpsiAshBeacon_EnsureFullyCollected:
                logger.info('Ash beacon not fully collected, ignore action point limit temporarily')
                self.config.OS_ACTION_POINT_PRESERVE = 0
            logger.attr('OS_ACTION_POINT_PRESERVE', self.config.OS_ACTION_POINT_PRESERVE)

            remain = get_os_reset_remain()
            yellow_coins_preserve = self.config.cross_get(keys=['OpsiHazard1Leveling', 'YellowCoinsPreserve'])
            last_day_ap_threshold = self.config.cross_get(keys=['OpsiHazard1Leveling', 'LastDayActionPointThreshold'])

            # Replenish yellow coins if below the preserve, limited tasks first and meow as fallback.
            # CL1 is the lowest priority task in the scheduler, so the replenish task runs first
            # after this task stops, and returns here when the replenish task is exhausted.
            # Do not replenish on the last day, yellow coins will be reset anyway.
            if remain > 0 and self.get_yellow_coins() < yellow_coins_preserve:
                replenish = self._pick_yellow_coins_task()
                if replenish is not None:
                    logger.info(f'Reach the limit of yellow coins, preserve={yellow_coins_preserve}, '
                                f'run {replenish} to replenish')
                    with self.config.multi_set():
                        self.config.task_call(replenish)
                    self.config.task_stop()
                else:
                    logger.warning('Reach the limit of yellow coins but no replenish task is available, '
                                   'continue running CL1')

            self.get_current_zone()

            # Preset action point to 70
            # When running CL1 oil is for running CL1, not meowfficer farming
            keep_current_ap = True
            if self.config.OpsiGeneral_BuyActionPointLimit > 0:
                keep_current_ap = False
            self.action_point_set(cost=70, keep_current_ap=keep_current_ap, check_rest_ap=True)

            # Last day (less than 1 day to OpSi reset):
            # if the total action points exceed the threshold, stop CL1 and burn action points
            # via meowfficer farming instead.
            if remain == 0 and self._action_point_total > last_day_ap_threshold:
                if self.is_in_opsi_explore():
                    logger.info('OpsiExplore is running, skip meowfficer farming to burn action points')
                else:
                    logger.info(f'Last day to OpSi reset, total action points {self._action_point_total} '
                                f'exceed threshold {last_day_ap_threshold}, '
                                f'run meowfficer farming to burn action points')
                    with self.config.multi_set():
                        self.config.task_call('OpsiMeowfficerFarming')
                    self.config.task_stop()

            if self.config.OpsiHazard1Leveling_TargetZone != 0:
                zone = self.config.OpsiHazard1Leveling_TargetZone
            else:
                zone = 22
            logger.hr(f'OS hazard 1 leveling, zone_id={zone}', level=1)
            if self.zone.zone_id != zone or not self.is_zone_name_hidden:
                self.globe_goto(self.name_to_zone(zone), types='SAFE', refresh=True)
            self.fleet_set(self.config.OpsiFleet_Fleet)
            self.run_strategic_search()

            self.handle_after_auto_search()
            self.config.check_task_switch()
