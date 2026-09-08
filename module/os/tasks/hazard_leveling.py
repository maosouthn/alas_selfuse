from datetime import datetime, timedelta

from module.config.utils import get_os_reset_remain
from module.exception import RequestHumanTakeover
from module.logger import logger
from module.os.map import OSMap


class OpsiHazard1Leveling(OSMap):
    # Tasks to replenish yellow coins, limited tasks first; shortcat (meowfficer farming)
    # is the fallback when no limited task can be run. Replenish until yellow coins reach
    # the GUI target YellowCoinsReturn, and the task stops CL1 rather than keep running it
    # with insufficient coins.
    LIMITED_REPLENISH_TASKS = ['OpsiStronghold', 'OpsiObscure', 'OpsiAbyssal']
    FALLBACK_REPLENISH_TASK = 'OpsiMeowfficerFarming'

    def _next_run(self, task):
        """
        Returns:
            datetime or None: Normalized NextRun of a task.
        """
        value = self.config.cross_get(keys=[task, 'Scheduler', 'NextRun'])
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            try:
                # Config stores NextRun as "2020-01-01 00:00:00"; fromisoformat
                # only accepts the 'T' separator in older Python versions.
                return datetime.fromisoformat(value.replace(' ', 'T'))
            except ValueError:
                return None
        return None

    def _replenish_source_available(self, task):
        """
        A replenish source is available when it is not delayed into the future.
        It may be disabled in the GUI — task_call(force_call=True) pulls it.

        Returns:
            bool
        """
        next_run = self._next_run(task)
        if next_run is None:
            return False
        if next_run > datetime.now():
            logger.info(f'{task} is delayed until {next_run}, skipped for yellow coins replenish')
            return False
        return True

    def _pick_yellow_coins_replenish_task(self):
        """
        Pick a task to replenish yellow coins, limited tasks first then shortcat.

        Returns:
            str or None: Task name to call, None if none is available.
        """
        if self.is_in_opsi_explore():
            logger.info('OpsiExplore is running, skip yellow coins replenish')
            return None
        for task in self.LIMITED_REPLENISH_TASKS:
            if self._replenish_source_available(task):
                return task
        if self._replenish_source_available(self.FALLBACK_REPLENISH_TASK):
            logger.info('No limited replenish task available, fallback to shortcat (meowfficer farming)')
            return self.FALLBACK_REPLENISH_TASK
        return None

    def _next_cooling_replenish_task(self):
        """
        Return the soonest replenish source that will become available within ~1 hour.
        Used to decide between waiting for a cooling down source and requesting human
        takeover when no source is immediately available.

        Returns:
            tuple[str, int] or (None, None): (task, minutes until available).
        """
        now = datetime.now()
        upcoming = []
        horizon = now + timedelta(minutes=61)
        for task in self.LIMITED_REPLENISH_TASKS + [self.FALLBACK_REPLENISH_TASK]:
            next_run = self._next_run(task)
            if next_run is not None and now < next_run <= horizon:
                upcoming.append((next_run, task))
        if not upcoming:
            return None, None
        upcoming.sort(key=lambda item: item[0])
        minutes = int((upcoming[0][0] - now).total_seconds() // 60)
        return upcoming[0][1], minutes

    def _restore_replenish_tasks(self):
        """
        task_call(force_call=True) auto-enables a replenish task in the runtime config.
        Restore every replenish source to disabled so the scheduler never runs them as
        ordinary tasks — they run only when CL1 dispatches them.
        """
        for task in self.LIMITED_REPLENISH_TASKS + [self.FALLBACK_REPLENISH_TASK]:
            if self.config.is_task_enabled(task):
                logger.info(f'Restore replenish task {task} to disabled')
                self.config.cross_set(keys=f'{task}.Scheduler.Enable', value=False)

    def os_hazard1_leveling(self):
        logger.hr('OS hazard 1 leveling', level=1)
        # Without these enabled, CL1 gains 0 profits
        self.config.override(
            OpsiGeneral_DoRandomMapEvent=True,
            OpsiGeneral_AkashiShopFilter='ActionPoint',
        )
        # Local tweak: upstream auto-enables 'OpsiMeowfficerFarming' here,
        # forcing shortcat on whenever CL1 runs. Keep the user's scheduler choice.
        while True:
            # Limited action point preserve of hazard 1 to 200
            self.config.OS_ACTION_POINT_PRESERVE = 200
            if self.config.is_task_enabled('OpsiAshBeacon') \
                    and not self._ash_fully_collected \
                    and self.config.cross_get("OpsiAshBeacon.OpsiAshBeacon.EnsureFullyCollected", True):
                logger.info('Ash beacon not fully collected, ignore action point limit temporarily')
                self.config.OS_ACTION_POINT_PRESERVE = 0
            logger.attr('OS_ACTION_POINT_PRESERVE', self.config.OS_ACTION_POINT_PRESERVE)

            # task_call() auto-enables the replenish tasks so the scheduler will run
            # them even though the user has them disabled in the GUI. Restore them to
            # disabled at the next CL1 round so they are never scheduled as ordinary
            # tasks (and recover cleanly if ALAS restarted mid-replenish).
            self._restore_replenish_tasks()

            remain = get_os_reset_remain()
            yellow_coins_preserve = self.config.cross_get(
                keys=['OpsiHazard1Leveling', 'OpsiHazard1Leveling', 'YellowCoinsPreserve'])
            yellow_coins_return = self.config.cross_get(
                keys=['OpsiHazard1Leveling', 'OpsiHazard1Leveling', 'YellowCoinsReturn'])
            last_day_ap_threshold = self.config.cross_get(
                keys=['OpsiHazard1Leveling', 'OpsiHazard1Leveling', 'LastDayActionPointThreshold'])

            # Replenish yellow coins until they reach the GUI target YellowCoinsReturn.
            # CL1 stops itself and hands over to the replenish task (limited tasks first,
            # shortcat as fallback). Below the trigger YellowCoinsPreserve with no source
            # that will be ready in the near future there is nothing to do but request
            # human takeover; if a source is only cooling down for roughly the next hour,
            # wait for it instead of bothering the user.
            # Do not replenish on the last day, yellow coins will be reset anyway.
            yellow = self.get_yellow_coins()
            if remain > 0 and yellow < yellow_coins_return:
                replenish = self._pick_yellow_coins_replenish_task()
                if replenish is not None:
                    logger.info(f'Yellow coins {yellow} below return target {yellow_coins_return}, '
                                f'run {replenish} to replenish')
                    with self.config.multi_set():
                        self.config.task_call(replenish)
                    self.config.task_stop()
                elif yellow < yellow_coins_preserve:
                    cooling, minutes = self._next_cooling_replenish_task()
                    if cooling is not None:
                        logger.info(
                            f'Yellow coins {yellow} below preserve {yellow_coins_preserve}, '
                            f'replenish source {cooling} cooling down (about {minutes} min), '
                            f'wait CL1 and re-check')
                        self.config.task_delay(minute=30)
                        self.config.task_stop()
                    else:
                        logger.critical(
                            'Yellow coins below preserve and no replenish task available '
                            'in the near future, request human takeover')
                        raise RequestHumanTakeover
                else:
                    logger.warning('Yellow coins below return target but no replenish task '
                                   'available, continue running CL1')

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
