"""Tests for the Operation Siren smart scheduling.

The bundled interpreter (`toolkit/python.exe`) ships the runtime dependencies
but no pytest, so the tests use plain asserts and can be run directly:

    toolkit\\python.exe tests\\os\\test_scheduling.py

pytest collects the same `test_*` functions when it is available.
"""
import os
import sys

sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))

from module.os.tasks.scheduling import OpsiScheduling
from module.os.tasks.task_context import is_proxied, opsi_task_context
from module.os_handler.action_point import ActionPointHandler


class FakeFunction:
    def __init__(self, command):
        self.command = command
        self.enable = True
        self.next_run = None


class FakeConfig:
    """Minimal stand-in for AzurLaneConfig, only what the scheduler reads."""

    def __init__(self, values=None, storage=None):
        self.data = {}
        self.values = dict(values or {})
        self.storage = dict(storage or {})
        self.task = FakeFunction('OpsiScheduling')
        self.overridden = {}
        self.bound = {}
        self.bound_to = []
        self.save_count = 0

    def cross_get(self, keys, default=None):
        if isinstance(keys, list):
            keys = '.'.join(keys)
        if keys == OpsiScheduling.CONFIG_PATH_STORAGE:
            return dict(self.storage) if self.storage else default
        return self.values.get(keys, default)

    def cross_set(self, keys, value):
        if isinstance(keys, list):
            keys = '.'.join(keys)
        if keys == OpsiScheduling.CONFIG_PATH_STORAGE:
            self.storage = dict(value)
            return
        self.values[keys] = value

    def save(self):
        self.save_count += 1

    def is_task_enabled(self, task):
        return bool(self.values.get('%s.Scheduler.Enable' % task, False))

    def bind(self, func, func_list=None):
        command = getattr(func, 'command', func)
        self.bound_to.append(command)
        self.bound = {'bound_to': command}


def make_scheduler(values=None):
    scheduler = object.__new__(OpsiScheduling)
    scheduler.config = FakeConfig(values=values)
    scheduler._get_opsi_reset_key = lambda: 'cycle-1'
    return scheduler


# ==================== Task identity proxy ====================

def test_task_context_switches_and_restores_identity():
    config = FakeConfig()
    original = config.task

    with opsi_task_context(config, 'OpsiHazard1Leveling'):
        assert config.task.command == 'OpsiHazard1Leveling'
        assert config._bind_task_override == 'OpsiHazard1Leveling'
        assert config._task_switch_owner.command == 'OpsiScheduling'
        assert is_proxied(config) is True
        assert config.bound_to[-1] == 'OpsiHazard1Leveling'

    assert config.task is original
    assert is_proxied(config) is False
    assert config.bound_to[-1] == 'OpsiScheduling'
    assert not hasattr(config, '_bind_task_override')
    assert not hasattr(config, '_task_switch_owner')


def test_task_context_rolls_back_overrides():
    config = FakeConfig()
    config.overridden['KeptArg'] = 'before'

    with opsi_task_context(config, 'OpsiMeowfficerFarming'):
        config.overridden['LeakedArg'] = 'during'
        config.LeakedArg = 'instance'

    assert config.overridden == {'KeptArg': 'before'}
    assert not hasattr(config, 'LeakedArg')


def test_task_context_nested_keeps_outermost_owner():
    config = FakeConfig()

    with opsi_task_context(config, 'OpsiHazard1Leveling'):
        owner = config._task_switch_owner
        with opsi_task_context(config, 'OpsiMeowfficerFarming'):
            assert config._task_switch_owner is owner
            assert config.task.command == 'OpsiMeowfficerFarming'
        assert config.task.command == 'OpsiHazard1Leveling'

    assert config.task.command == 'OpsiScheduling'


def test_task_context_restores_on_exception():
    config = FakeConfig()
    original = config.task

    try:
        with opsi_task_context(config, 'OpsiHazard1Leveling'):
            raise ValueError('sub task failed')
    except ValueError:
        pass

    assert config.task is original
    assert is_proxied(config) is False


# ==================== Yellow coins state machine ====================

CONFIG_ENABLE_ALL = {
    'OpsiScheduling.OpsiScheduling.EnableStronghold': True,
    'OpsiScheduling.OpsiScheduling.EnableObscure': True,
    'OpsiScheduling.OpsiScheduling.EnableAbyssal': True,
    'OpsiScheduling.OpsiScheduling.EnableMeowfficerFarming': True,
}


def test_replenish_state_machine():
    scheduler = make_scheduler()
    preserve, target = 40000, 60000

    scheduler._sync_replenish_state(50000, preserve, target)
    assert scheduler._is_replenish_active() is False

    scheduler._sync_replenish_state(30000, preserve, target)
    assert scheduler._is_replenish_active() is True

    # Reaching the preserve alone must not end an ongoing replenish.
    scheduler._sync_replenish_state(45000, preserve, target)
    assert scheduler._is_replenish_active() is True

    scheduler._sync_replenish_state(60000, preserve, target)
    assert scheduler._is_replenish_active() is False


def test_replenish_state_dropped_on_new_cycle():
    scheduler = make_scheduler()
    scheduler._sync_replenish_state(30000, 40000, 60000)
    assert scheduler._is_replenish_active() is True

    # New OpSi cycle: the breakpoint is stale, coins in between must not keep
    # the scheduler replenishing.
    scheduler._get_opsi_reset_key = lambda: 'cycle-2'
    scheduler._sync_replenish_state(45000, 40000, 60000)
    assert scheduler._is_replenish_active() is False


# ==================== Coin thresholds ====================

CL1_COINS = {
    'OpsiHazard1Leveling.OpsiHazard1Leveling.YellowCoinsPreserve': 10200,
    'OpsiHazard1Leveling.OpsiHazard1Leveling.YellowCoinsReturn': 20200,
}


def test_coin_targets_follow_cl1_when_scheduler_values_are_zero():
    scheduler = make_scheduler(dict(CL1_COINS))
    # Enabling the scheduler must not silently change the coins behaviour the
    # user already tuned on the hazard 1 leveling page.
    assert scheduler._get_coin_targets() == (10200, 20200)


def test_coin_targets_come_from_the_scheduler_config():
    values = dict(CL1_COINS)
    values['OpsiScheduling.OpsiScheduling.OperationCoinsPreserve'] = 40000
    values['OpsiScheduling.OpsiScheduling.OperationCoinsReturnThreshold'] = 20000
    scheduler = make_scheduler(values)
    assert scheduler._get_coin_targets() == (40000, 60000)


def test_coin_targets_clamp_target_to_preserve():
    values = {
        'OpsiScheduling.OpsiScheduling.OperationCoinsPreserve': 30000,
        'OpsiHazard1Leveling.OpsiHazard1Leveling.YellowCoinsReturn': 20200,
    }
    scheduler = make_scheduler(values)
    preserve, target = scheduler._get_coin_targets()
    assert preserve == 30000
    assert target == 30000


def test_coin_targets_survive_missing_cl1_settings():
    scheduler = make_scheduler({})
    preserve, target = scheduler._get_coin_targets()
    assert preserve == 40000
    assert target == 60000


# ==================== Coin task selection ====================

def test_coin_task_selection_uses_switches_and_priority():
    values = dict(CONFIG_ENABLE_ALL)
    values['OpsiScheduling.OpsiScheduling.EnableStronghold'] = False
    values['OpsiScheduling.OpsiScheduling.TaskPriority'] = \
        'OpsiAbyssal > OpsiObscure > OpsiMeowfficerFarming'
    scheduler = make_scheduler(values)

    # Stronghold is switched off, the rest follows TaskPriority.
    assert scheduler._get_enabled_coin_tasks() == \
        ['OpsiAbyssal', 'OpsiObscure', 'OpsiMeowfficerFarming']


def test_coin_task_selection_ignores_scheduler_next_run():
    values = dict(CONFIG_ENABLE_ALL)
    scheduler = make_scheduler(values)
    # A disabled task keeps its NextRun in the past, which is exactly why the
    # scheduler NextRun must not be used as the availability check.
    assert scheduler._get_available_coin_tasks() == \
        ['OpsiStronghold', 'OpsiObscure', 'OpsiAbyssal', 'OpsiMeowfficerFarming']


def test_coin_task_no_content_is_skipped_without_touching_schedule():
    scheduler = make_scheduler(dict(CONFIG_ENABLE_ALL))
    scheduler._mark_coin_task_no_content('OpsiObscure')

    available = scheduler._get_available_coin_tasks()
    assert 'OpsiObscure' not in available
    assert available == ['OpsiStronghold', 'OpsiAbyssal', 'OpsiMeowfficerFarming']
    # The scheduler never rewrites the task's own schedule.
    assert 'OpsiObscure.Scheduler.NextRun' not in scheduler.config.values

    scheduler._clear_coin_task_no_content('OpsiObscure')
    assert 'OpsiObscure' in scheduler._get_available_coin_tasks()


def test_coin_task_no_content_expires():
    scheduler = make_scheduler(dict(CONFIG_ENABLE_ALL))
    scheduler._get_no_content_skip_hours = lambda: 0
    scheduler._mark_coin_task_no_content('OpsiObscure')
    # A zero skip window means the mark never hides the task.
    assert 'OpsiObscure' in scheduler._get_available_coin_tasks()


def test_no_enabled_coin_task():
    scheduler = make_scheduler({})
    assert scheduler._get_enabled_coin_tasks() == []
    assert scheduler._get_available_coin_tasks() == []


# ==================== Action point boxes ====================

class FakeActionPointConfig:
    OS_ACTION_POINT_PRESERVE = 0
    OpsiGeneral_BuyActionPointLimit = 0
    OpsiGeneral_OilLimit = 0
    OS_ACTION_POINT_BOX_USE = True


def make_action_point_handler(current, total, boxes):
    handler = object.__new__(ActionPointHandler)
    handler.config = FakeActionPointConfig()
    handler._action_point_current = current
    handler._action_point_total = total
    handler._action_point_box = list(boxes)
    used = []
    reads = []
    handler._is_in_action_point = lambda: True
    handler.action_point_safe_get = lambda: reads.append(1)
    handler.action_point_quit = lambda: None
    handler.action_point_use = lambda: None
    handler.action_point_set_button = lambda index: used.append(index)
    return handler, used, reads


def test_avoid_ap_overflow_uses_smallest_box_first():
    handler, used, _ = make_action_point_handler(60, 200, [0, 1, 1, 1])
    result = handler.handle_action_point(
        None, None, cost=70, keep_current_ap=False, avoid_ap_overflow=True)
    # The reading never changes in this stub, so the loop gives up after 12 runs.
    assert result is False
    # 20 AP box first, the 50 and 100 boxes stay for the following rounds.
    assert used[0] == 1
    assert len(used) == 12


def test_default_order_uses_biggest_box_first():
    handler, used, _ = make_action_point_handler(60, 200, [0, 1, 1, 1])
    handler.handle_action_point(None, None, cost=70, keep_current_ap=False)
    assert used[0] == 3


def test_skip_first_read_reuses_reading():
    handler, _, reads = make_action_point_handler(120, 200, [0, 0, 0, 0])
    result = handler.handle_action_point(
        None, None, cost=70, keep_current_ap=False, skip_first_read=True)
    assert result is True
    assert reads == []


def test_first_read_happens_by_default():
    handler, _, reads = make_action_point_handler(120, 200, [0, 0, 0, 0])
    result = handler.handle_action_point(None, None, cost=70, keep_current_ap=False)
    assert result is True
    assert reads == [1]


def test_action_point_reusable():
    handler, _, _ = make_action_point_handler(120, 200, [0, 0, 0, 0])
    assert handler.action_point_reusable(None, cost=70) is False
    assert handler.action_point_reusable((200, 120), cost=70) is True
    assert handler.action_point_reusable((200, 70), cost=70) is True
    assert handler.action_point_reusable((200, 69), cost=70) is False


def test_action_point_reusable_respects_preserve():
    class PreserveConfig(FakeActionPointConfig):
        OS_ACTION_POINT_PRESERVE = 300

    handler, _, _ = make_action_point_handler(120, 200, [0, 0, 0, 0])
    handler.config = PreserveConfig()
    # Total is below the preserve, the popup path has to intercept it.
    assert handler.action_point_reusable((200, 120), cost=70) is False


# ==================== Config integration ====================

def test_update_binds_the_proxied_task():
    from module.config.config import AzurLaneConfig

    config = object.__new__(AzurLaneConfig)
    config.task = FakeFunction('OpsiScheduling')
    config._bind_task_override = 'OpsiHazard1Leveling'
    bound_to = []
    config.load = lambda: None
    config.config_override = lambda: None
    config.save = lambda: None
    config.bind = lambda func, func_list=None: bound_to.append(getattr(func, 'command', func))

    config.update()
    assert bound_to == ['OpsiHazard1Leveling']


def test_task_switched_compares_against_the_owner():
    from module.config.config import AzurLaneConfig, name_to_function

    config = object.__new__(AzurLaneConfig)
    config.stop_event = None
    config.task = name_to_function('OpsiHazard1Leveling')
    config._task_switch_owner = name_to_function('OpsiScheduling')
    config.load = lambda: None
    # The scheduler itself is still the pending task while it proxies a sub task.
    config.get_next = lambda: name_to_function('OpsiScheduling')

    assert config.task_switched() is False

    # Without the owner the proxied sub task would mistake the pending scheduler
    # task for a preemption and stop itself in the middle of a round.
    del config._task_switch_owner
    assert config.task_switched() is True


if __name__ == '__main__':
    import traceback

    tests = [(name, func) for name, func in sorted(globals().items())
             if name.startswith('test_') and callable(func)]
    failed = 0
    for name, func in tests:
        try:
            func()
            print('PASS %s' % name)
        except Exception:
            failed += 1
            print('FAIL %s' % name)
            traceback.print_exc()
    print('\n%d passed, %d failed, %d total' % (len(tests) - failed, failed, len(tests)))
    sys.exit(1 if failed else 0)
