"""
Operation Siren smart scheduling (OpsiScheduling).

Hazard 1 leveling burns action points for ship experience, yellow coins are
spent in the shops to buy action points back, and the coin income is negative,
so yellow coins have to be replenished in the other zones from time to time.

Doing that with task switching costs a full Operation Siren re-initialization
per round and makes the replenish sources run for nothing, because a task which
is not enabled still looks available while its NextRun stays in the past. This
scheduler therefore owns one Operation Siren session and runs every sub task one
round at a time under its own task identity, without switching tasks and without
re-initializing.

Decision flow per round:
- OpsiExplore is running          -> delay the scheduler, exploration comes first
- Last day of the OpSi cycle      -> burn action points with shortcat
- Yellow coins below the preserve -> replenish until preserve + return threshold
- Otherwise                       -> run one round of hazard 1 leveling
"""

from datetime import datetime, timedelta

from module.config.config import TaskEnd
from module.config.utils import get_os_reset_remain
from module.logger import logger
from module.os.map import OSMap
from module.os.tasks.hazard_leveling import OpsiHazard1Leveling
from module.os.tasks.task_context import opsi_task_context
from module.os_handler.action_point import ActionPointLimit


class OpsiScheduling(OSMap):
    # Task commands
    TASK_NAME_SCHEDULING = 'OpsiScheduling'
    TASK_NAME_HAZARD1_LEVELING = 'OpsiHazard1Leveling'
    TASK_NAME_MEOWFFICER_FARMING = 'OpsiMeowfficerFarming'
    TASK_NAME_OBSCURE = 'OpsiObscure'
    TASK_NAME_ABYSSAL = 'OpsiAbyssal'
    TASK_NAME_STRONGHOLD = 'OpsiStronghold'

    TASK_NAMES = {
        TASK_NAME_MEOWFFICER_FARMING: 'shortcat (meowfficer farming)',
        TASK_NAME_OBSCURE: 'obscure zone',
        TASK_NAME_ABYSSAL: 'abyssal zone',
        TASK_NAME_STRONGHOLD: 'siren stronghold',
    }

    # Config paths, read through cross_get so they work regardless of the
    # currently bound task.
    CONFIG_PATH_PRESERVE = 'OpsiScheduling.OpsiScheduling.OperationCoinsPreserve'
    CONFIG_PATH_RETURN_THRESHOLD = 'OpsiScheduling.OpsiScheduling.OperationCoinsReturnThreshold'
    CONFIG_PATH_CL1_AP_RESERVE = 'OpsiScheduling.OpsiScheduling.Cl1ActionPointReserve'
    CONFIG_PATH_COIN_TASK_AP_PRESERVE = 'OpsiScheduling.OpsiScheduling.ActionPointPreserve'
    CONFIG_PATH_TASK_PRIORITY = 'OpsiScheduling.OpsiScheduling.TaskPriority'
    CONFIG_PATH_NO_CONTENT_SKIP_HOURS = 'OpsiScheduling.OpsiScheduling.NoContentSkipHours'
    CONFIG_PATH_STORAGE = 'OpsiScheduling.Storage.Storage'
    CONFIG_PATH_ENABLE = {
        TASK_NAME_STRONGHOLD: 'OpsiScheduling.OpsiScheduling.EnableStronghold',
        TASK_NAME_OBSCURE: 'OpsiScheduling.OpsiScheduling.EnableObscure',
        TASK_NAME_ABYSSAL: 'OpsiScheduling.OpsiScheduling.EnableAbyssal',
        TASK_NAME_MEOWFFICER_FARMING: 'OpsiScheduling.OpsiScheduling.EnableMeowfficerFarming',
    }
    # Fallbacks used when the scheduler config is missing, so the task is still
    # usable with an older config file.
    FALLBACK_PRESERVE_PATH = 'OpsiHazard1Leveling.OpsiHazard1Leveling.YellowCoinsPreserve'
    FALLBACK_RETURN_PATH = 'OpsiHazard1Leveling.OpsiHazard1Leveling.YellowCoinsReturn'
    FALLBACK_LAST_DAY_PATH = 'OpsiHazard1Leveling.OpsiHazard1Leveling.LastDayActionPointThreshold'
    FALLBACK_CL1_AP_RESERVE = 200
    FALLBACK_COIN_TASK_AP_PRESERVE = 0
    FALLBACK_NO_CONTENT_SKIP_HOURS = 6
    DEFAULT_TASK_PRIORITY = 'OpsiStronghold > OpsiObscure > OpsiAbyssal > OpsiMeowfficerFarming'

    # Storage keys
    STATE_KEY_COIN_REPLENISH = 'CoinReplenish'
    STATE_KEY_NO_CONTENT = 'CoinTaskNoContent'

    # Set by proxied coin tasks when they find nothing to do this round.
    _coin_task_no_content = None

    # ==================== Config helpers ====================

    def _config_value(self, key, default=None):
        """
        Read a config value by its full path.

        Args:
            key (str): Config path, such as 'OpsiScheduling.OpsiScheduling.EnableObscure'.
            default: Value returned when the key is missing.

        Returns:
            The configured value, or `default`.
        """
        value = self.config.cross_get(keys=key, default=None)
        return default if value is None else value

    def _config_enabled(self, key, default=False):
        """
        Read a checkbox config value, tolerating the historical WebUI values.

        Args:
            key (str): Config path of the checkbox.
            default (bool): Value returned when the key is missing.

        Returns:
            bool: If the checkbox is enabled.
        """
        value = self.config.cross_get(keys=key, default=default)
        if isinstance(value, list):
            return any(bool(item) for item in value)
        return value is True

    def _int_config(self, key, default=0):
        """
        Read an integer config value by its full path.

        Args:
            key (str): Config path.
            default (int): Value returned when the key is missing or unusable.

        Returns:
            int:
        """
        value = self.config.cross_get(keys=key, default=None)
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def _get_coin_targets(self):
        """
        Yellow coins thresholds used by the decision of this round.

        Both scheduler values may stay at 0, then the thresholds configured on
        the hazard 1 leveling page are used as they are, so enabling the
        scheduler never silently changes the coins behaviour already tuned there.

        Returns:
            tuple[int, int]: (preserve, target). Replenishing starts below the
                preserve, hazard 1 leveling resumes at the target.
        """
        preserve = self._int_config(self.CONFIG_PATH_PRESERVE, 0)
        threshold = self._int_config(self.CONFIG_PATH_RETURN_THRESHOLD, 0)

        if preserve <= 0:
            preserve = self._int_config(self.FALLBACK_PRESERVE_PATH, 40000)
            logger.info(f'[OS scheduling] No coins preserve configured, '
                        f'follow hazard 1 leveling: {preserve}')

        if threshold > 0:
            return preserve, preserve + threshold

        target = self._int_config(self.FALLBACK_RETURN_PATH, 0)
        if target <= 0:
            target = preserve + 20000
        logger.info(f'[OS scheduling] No coins return threshold configured, '
                    f'follow hazard 1 leveling target: {target}')
        return preserve, max(target, preserve)

    def _get_cl1_ap_reserve(self):
        """
        Returns:
            int: Action points kept for hazard 1 leveling.
        """
        value = self.config.cross_get(keys=self.CONFIG_PATH_CL1_AP_RESERVE, default=None)
        if value is None:
            return self.FALLBACK_CL1_AP_RESERVE
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return self.FALLBACK_CL1_AP_RESERVE

    def _get_coin_task_ap_preserve(self):
        """
        Returns:
            int | None: Action points preserved while replenishing, or None to
                keep each coin task's own setting.
        """
        value = self.config.cross_get(keys=self.CONFIG_PATH_COIN_TASK_AP_PRESERVE, default=None)
        try:
            value = int(value)
        except (TypeError, ValueError):
            return None
        return value if value > 0 else None

    def _get_last_day_ap_threshold(self):
        """
        Returns:
            int: Total action points above which the last day of the OpSi cycle
                burns action points with shortcat.
        """
        value = self.config.cross_get(keys=self.FALLBACK_LAST_DAY_PATH, default=10000)
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 10000

    def _get_no_content_skip_hours(self):
        """
        Returns:
            int: Hours a coin task is skipped after it reported no content.
        """
        value = self.config.cross_get(keys=self.CONFIG_PATH_NO_CONTENT_SKIP_HOURS, default=None)
        try:
            hours = int(value)
        except (TypeError, ValueError):
            return self.FALLBACK_NO_CONTENT_SKIP_HOURS
        return hours if hours > 0 else self.FALLBACK_NO_CONTENT_SKIP_HOURS

    # ==================== Storage helpers ====================

    def _get_storage_state(self):
        """
        Returns:
            dict: Persisted scheduler state.
        """
        value = self.config.cross_get(keys=self.CONFIG_PATH_STORAGE, default=None)
        return dict(value) if isinstance(value, dict) else {}

    def _set_storage_state(self, state):
        """
        Persist the scheduler state immediately, it survives a restart.

        Args:
            state (dict): State to write.
        """
        self.config.cross_set(keys=self.CONFIG_PATH_STORAGE, value=state)
        self.config.save()

    # ==================== Yellow coins state machine ====================

    def _reset_state_for_new_month(self, state):
        """
        Drop the replenish breakpoint when a new OpSi cycle started.

        Args:
            state (dict): Scheduler state, mutated in place.

        Returns:
            bool: If the state was reset.
        """
        current = state.get(self.STATE_KEY_COIN_REPLENISH)
        if not isinstance(current, dict):
            return False
        reset = self._get_opsi_reset_key()
        if current.get('reset') == reset:
            return False
        logger.info('[OS scheduling] OpSi cycle changed, drop the coins replenish breakpoint')
        state.pop(self.STATE_KEY_COIN_REPLENISH, None)
        return True

    @staticmethod
    def _get_opsi_reset_key():
        """
        Returns:
            str: Identifier of the current OpSi cycle.
        """
        from module.config.utils import get_os_next_reset
        return get_os_next_reset().isoformat()

    def _is_replenish_active(self):
        """
        Returns:
            bool: If the scheduler is in the middle of a replenish phase.
        """
        state = self._get_storage_state()
        return isinstance(state.get(self.STATE_KEY_COIN_REPLENISH), dict)

    def _sync_replenish_state(self, yellow_coins, preserve, target):
        """
        Enter or leave the replenish phase according to the current coins.

        Being in the replenish phase is persisted, so a restart in the middle of
        it keeps replenishing until the target is reached instead of bouncing
        back to hazard 1 leveling with too few coins.

        Args:
            yellow_coins (int): Current yellow coins.
            preserve (int): Coins preserve, below which replenishing starts.
            target (int): Coins target, at which hazard 1 leveling resumes.
        """
        state = self._get_storage_state()
        dirty = self._reset_state_for_new_month(state)
        current = state.get(self.STATE_KEY_COIN_REPLENISH)

        if isinstance(current, dict):
            if yellow_coins >= target:
                logger.info(f'[OS scheduling] Yellow coins {yellow_coins} reached the target '
                            f'{target}, back to hazard 1 leveling')
                state.pop(self.STATE_KEY_COIN_REPLENISH, None)
                dirty = True
        elif yellow_coins < preserve:
            logger.info(f'[OS scheduling] Yellow coins {yellow_coins} below the preserve '
                        f'{preserve}, replenish until {target}')
            state[self.STATE_KEY_COIN_REPLENISH] = {
                'reset': self._get_opsi_reset_key(),
                'start': int(yellow_coins),
            }
            dirty = True

        if dirty:
            self._set_storage_state(state)

    # ==================== Coin task selection ====================

    def _get_enabled_coin_tasks(self):
        """
        List the replenish tasks the user enabled, ordered by priority.

        Only the explicit switches matter. The scheduler NextRun is deliberately
        ignored: a task which is not enabled keeps its NextRun in the past and
        would always look available, which is exactly how the replenish sources
        used to run for nothing.

        Returns:
            list[str]: Enabled task commands, most preferred first.
        """
        enabled = [task for task in self.CONFIG_PATH_ENABLE
                   if self._config_enabled(self.CONFIG_PATH_ENABLE[task])]
        if not enabled:
            return []

        priority = self.config.cross_get(keys=self.CONFIG_PATH_TASK_PRIORITY, default=None)
        if not isinstance(priority, str) or not priority.strip():
            priority = self.DEFAULT_TASK_PRIORITY
        order = [item.strip() for item in priority.split('>') if item.strip()]

        def sort_key(task):
            try:
                return order.index(task)
            except ValueError:
                return len(order)

        return sorted(enabled, key=sort_key)

    def _get_available_coin_tasks(self):
        """
        Filter out the coin tasks which reported no content recently.

        Returns:
            list[str]: Tasks worth trying this round.
        """
        tasks = self._get_enabled_coin_tasks()
        if not tasks:
            return []

        state = self._get_storage_state()
        no_content = state.get(self.STATE_KEY_NO_CONTENT)
        if not isinstance(no_content, dict):
            return tasks

        skip = timedelta(hours=self._get_no_content_skip_hours())
        now = datetime.now()
        available = []
        for task in tasks:
            checked_at = no_content.get(task)
            if not isinstance(checked_at, str):
                available.append(task)
                continue
            try:
                checked_at = datetime.fromisoformat(checked_at)
            except ValueError:
                available.append(task)
                continue
            if now - checked_at < skip:
                logger.info(f'[OS scheduling] {self.TASK_NAMES.get(task, task)} had no content at '
                            f'{checked_at}, skipped for {skip}')
                continue
            available.append(task)
        return available

    def _mark_coin_task_no_content(self, task_name):
        """
        Remember that a coin task has nothing to do, without touching its own
        scheduler state.

        Args:
            task_name (str): Task command.
        """
        state = self._get_storage_state()
        no_content = state.get(self.STATE_KEY_NO_CONTENT)
        if not isinstance(no_content, dict):
            no_content = {}
        no_content[task_name] = datetime.now().isoformat()
        state[self.STATE_KEY_NO_CONTENT] = no_content
        self._set_storage_state(state)

    def _clear_coin_task_no_content(self, task_name):
        """
        Forget the no-content mark of a coin task after it did some work.

        Args:
            task_name (str): Task command.
        """
        state = self._get_storage_state()
        no_content = state.get(self.STATE_KEY_NO_CONTENT)
        if not isinstance(no_content, dict) or task_name not in no_content:
            return
        no_content.pop(task_name, None)
        state[self.STATE_KEY_NO_CONTENT] = no_content
        self._set_storage_state(state)

    # ==================== Action point helpers ====================

    def _close_scheduling_action_point(self):
        """
        Close the action point popup kept open for the decision.

        Pages:
            in: ACTION_POINT_USE if the popup was kept open
            out: page_os
        """
        if getattr(self, '_scheduling_ap_panel_open', False):
            self._scheduling_ap_panel_open = False
            self.action_point_quit()

    def _get_scheduling_action_point(self, *, keep_open=False):
        """
        Read the action points needed for the decision.

        Args:
            keep_open (bool): Keep the popup open, so the top up of the chosen
                sub task happens in the popup already opened here.

        Returns:
            tuple[int, int]: (total action points, current action points).

        Pages:
            in: page_os
            out: ACTION_POINT_USE if keep_open, else page_os
        """
        self._close_scheduling_action_point()
        self.action_point_enter()
        self.action_point_safe_get()
        if keep_open:
            self._scheduling_ap_panel_open = True
            self._scheduling_ap_box_use = self.config.OS_ACTION_POINT_BOX_USE
        else:
            self.action_point_quit()
        return (
            int(getattr(self, '_action_point_total', 0) or 0),
            int(getattr(self, '_action_point_current', 0) or 0),
        )

    def _prepare_action_point(self, fresh_ap, *, cost, avoid_ap_overflow=False):
        """
        Top the action points up in the popup already opened for the decision.

        Args:
            fresh_ap (tuple[int, int] | None): Reading taken for the decision.
            cost (int): Action points the chosen sub task needs to start.
            avoid_ap_overflow (bool): Handle the top up the hazard 1 leveling way.

        Returns:
            tuple[int, int] | None: Reading after the top up, or the incoming
                reading when the popup was already reusable, or None when the
                popup is not available any more.

        Pages:
            in: ACTION_POINT_USE if the popup was kept open
            out: page_os
        """
        if not getattr(self, '_scheduling_ap_panel_open', False):
            return fresh_ap
        self._scheduling_ap_panel_open = False
        if not self._is_in_action_point():
            return None

        same_box_use = self._scheduling_ap_box_use == self.config.OS_ACTION_POINT_BOX_USE
        if same_box_use and self.action_point_reusable(fresh_ap, cost, avoid_ap_overflow):
            self.action_point_quit()
            return fresh_ap

        logger.info('[OS scheduling] Top up the action points in the popup opened for the decision')
        if not self.handle_action_point(
            zone=None, pinned=None, cost=cost, keep_current_ap=True,
            check_rest_ap=True, avoid_ap_overflow=avoid_ap_overflow,
            skip_first_read=same_box_use,
        ):
            self.action_point_quit()
            return None
        return (
            int(getattr(self, '_action_point_total', 0) or 0),
            int(getattr(self, '_action_point_current', 0) or 0),
        )

    def _delay_for_ap_limit(self, total_ap, min_ap_reserve):
        """
        Delay the scheduler because there are not enough action points.

        Args:
            total_ap (int): Current total action points.
            min_ap_reserve (int): Minimum action points to keep.
        """
        logger.warning(f'[OS scheduling] Action points reached the minimum reserve '
                       f'({total_ap} <= {min_ap_reserve}), delay the scheduler')
        self.config.task_delay(server_update=True)
        self.config.task_stop()

    # ==================== Sub task proxies ====================

    def _run_coin_task_once(self, task_name, total_ap, current_ap):
        """
        Run one round of a coin replenish task under its own task identity.

        Args:
            task_name (str): Task command to run.
            total_ap (int): Total action points read for the decision.
            current_ap (int): Current action points read for the decision.

        Returns:
            bool: True if the task did some work, False if it has no content.
        """
        display = self.TASK_NAMES.get(task_name, task_name)
        logger.hr(f'OS scheduling: {display}', level=1)

        ap_preserve = self._get_coin_task_ap_preserve()
        if ap_preserve is None:
            ap_preserve = 0
        fresh_ap = self._prepare_action_point(
            (total_ap, current_ap), cost=0, avoid_ap_overflow=False,
        )

        self._coin_task_no_content = None
        no_content = False
        with self.config.temporary(OS_ACTION_POINT_PRESERVE=ap_preserve):
            with opsi_task_context(self.config, task_name):
                try:
                    if task_name == self.TASK_NAME_MEOWFFICER_FARMING:
                        self.run_meowfficer_farming_once(fresh_ap=fresh_ap)
                    elif task_name == self.TASK_NAME_OBSCURE:
                        self.clear_obscure()
                    elif task_name == self.TASK_NAME_ABYSSAL:
                        self.clear_abyssal()
                    elif task_name == self.TASK_NAME_STRONGHOLD:
                        self.clear_stronghold()
                    else:
                        logger.error(f'[OS scheduling] Unable to run coin task: {task_name}')
                        return False
                except TaskEnd:
                    # A sub task may stop itself, for example when its own
                    # resource is exhausted. Only a real preemption ends the
                    # scheduler itself.
                    if self.config.task_switched():
                        raise
                    logger.info(f'[OS scheduling] {display} stopped this round')
                    no_content = True
                finally:
                    no_content = no_content or self._coin_task_no_content == task_name
                    self._coin_task_no_content = None

        if no_content:
            logger.info(f'[OS scheduling] {display} has no content, skip it for '
                        f'{self._get_no_content_skip_hours()} hours')
            self._mark_coin_task_no_content(task_name)
            return False

        self._clear_coin_task_no_content(task_name)
        return True

    def _run_hazard1_once(self, ap_reserve, total_ap, current_ap):
        """
        Run one round of hazard 1 leveling under its own task identity.

        Args:
            ap_reserve (int): Action points kept for hazard 1 leveling.
            total_ap (int): Total action points read for the decision.
            current_ap (int): Current action points read for the decision.
        """
        logger.hr('OS scheduling: hazard 1 leveling', level=1)
        fresh_ap = self._prepare_action_point(
            (total_ap, current_ap), cost=OpsiHazard1Leveling.ACTION_POINT_COST,
            avoid_ap_overflow=True,
        )
        with self.config.temporary(OS_ACTION_POINT_PRESERVE=int(ap_reserve)):
            with opsi_task_context(self.config, self.TASK_NAME_HAZARD1_LEVELING):
                self.run_hazard1_leveling_once(fresh_ap=fresh_ap)

    # ==================== Decision ====================

    def _delay_for_opsi_explore(self):
        """
        Delay the scheduler while the monthly exploration is running.

        Returns:
            bool: If the scheduler was delayed.
        """
        if not self.is_in_opsi_explore():
            return False
        logger.info('[OS scheduling] OpsiExplore is running, delay smart scheduling')
        self.config.task_delay(server_update=True)
        self.config.task_stop()
        return True

    def _run_scheduling_decision(self, yellow_coins, total_ap, current_ap):
        """
        Decide what to run this round and run it.

        Args:
            yellow_coins (int): Yellow coins read for this round.
            total_ap (int): Total action points read for this round.
            current_ap (int): Current action points read for this round.
        """
        preserve, target = self._get_coin_targets()
        self._sync_replenish_state(yellow_coins, preserve, target)

        logger.info(f'[OS scheduling] Yellow coins: {yellow_coins}, preserve: {preserve}, '
                    f'target: {target}, replenishing: {self._is_replenish_active()}, '
                    f'action points: {total_ap} (current {current_ap})')

        # Last day of the OpSi cycle: burn the action points with shortcat
        # instead of starting another leveling round they would not finish.
        if get_os_reset_remain() == 0:
            last_day_threshold = self._get_last_day_ap_threshold()
            if total_ap > last_day_threshold:
                logger.info(f'[OS scheduling] Last day to OpSi reset, total action points '
                            f'{total_ap} exceed {last_day_threshold}, burn them with shortcat')
                self._run_coin_task_once(self.TASK_NAME_MEOWFFICER_FARMING, total_ap, current_ap)
                return

        if self._is_replenish_active():
            available = self._get_available_coin_tasks()
            if not available and not self._get_enabled_coin_tasks():
                logger.warning('[OS scheduling] No coin replenish task is enabled, '
                               'keep running hazard 1 leveling')
            elif not available:
                logger.warning('[OS scheduling] Every coin replenish task is marked as no content, '
                               'keep running hazard 1 leveling')
            else:
                ap_preserve = self._get_coin_task_ap_preserve()
                if ap_preserve is not None and total_ap <= ap_preserve:
                    self._delay_for_ap_limit(total_ap, ap_preserve)
                    return
                for task_name in available:
                    if self._run_coin_task_once(task_name, total_ap, current_ap):
                        return
                    current_ap = total_ap
                logger.warning('[OS scheduling] Every coin replenish task has no content this round, '
                               'keep running hazard 1 leveling')

        cl1_ap_reserve = self._get_cl1_ap_reserve()
        if total_ap <= cl1_ap_reserve:
            self._delay_for_ap_limit(total_ap, cl1_ap_reserve)
            return
        self._run_hazard1_once(cl1_ap_reserve, total_ap, current_ap)

    def run_scheduling_once(self):
        """
        Run one scheduling round.

        Pages:
            in: page_os
            out: page_os
        """
        if self._delay_for_opsi_explore():
            return

        yellow_coins = self.get_yellow_coins()
        try:
            total_ap, current_ap = self._get_scheduling_action_point(keep_open=True)
        except ActionPointLimit:
            logger.info('[OS scheduling] Unable to read the action points, delay the scheduler')
            self.config.task_delay(server_update=True)
            self.config.task_stop()
            return

        try:
            self._run_scheduling_decision(yellow_coins, total_ap, current_ap)
        except ActionPointLimit:
            logger.warning('[OS scheduling] Not enough action points for this round')
            self._delay_for_ap_limit(total_ap, self._get_cl1_ap_reserve())
        finally:
            self._close_scheduling_action_point()

    def run_smart_scheduling(self):
        """
        Run the smart scheduling loop.

        The loop owns one Operation Siren session and runs every sub task one
        round at a time. It yields to the other tasks through check_task_switch()
        once their NextRun is due, the scheduler itself stays pending in the
        meantime.
        """
        logger.hr('OS smart scheduling', level=1)
        while True:
            self.run_scheduling_once()
            self.config.check_task_switch()

    def os_scheduling(self):
        """
        Task entry, called by OperationSiren.os_scheduling().
        """
        self.run_smart_scheduling()
