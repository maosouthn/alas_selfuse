"""Tests for the Operation Siren resource watch behind the Overview dashboard.

The bundled interpreter (`toolkit/python.exe`) ships the runtime dependencies
but no pytest, so the tests use plain asserts and can be run directly:

    toolkit\\python.exe tests\\os\\test_resource_watch.py

pytest collects the same `test_*` functions when it is available.
"""
import os
import sys

sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))

from module.os.resource_watch import (
    CONFIG_PATH_STORAGE,
    get_coins_preserve,
    read_dashboard,
    record_action_point,
    record_yellow_coins,
)


class FakeConfig:
    """Minimal stand-in for AzurLaneConfig, only what the resource watch uses."""

    def __init__(self, values=None, storage=None):
        self.values = dict(values or {})
        self.storage = dict(storage or {})
        self.save_count = 0

    def cross_get(self, keys, default=None):
        if isinstance(keys, list):
            keys = '.'.join(keys)
        if keys == CONFIG_PATH_STORAGE:
            return dict(self.storage) if self.storage else default
        return self.values.get(keys, default)

    def cross_set(self, keys, value):
        if isinstance(keys, list):
            keys = '.'.join(keys)
        if keys == CONFIG_PATH_STORAGE:
            self.storage = dict(value)
            return
        self.values[keys] = value

    def save(self):
        self.save_count += 1


def test_record_action_point_writes_and_throttles():
    config = FakeConfig()
    record_action_point(config, 120, 200)

    assert config.save_count == 1
    watch = config.storage['ResourceWatch']
    assert watch['ActionPoint'] == 120
    assert watch['ActionPointTotal'] == 200
    assert 'Record' in watch

    # The popup is read again on every screenshot, a repeated reading must not
    # touch the config file.
    record_action_point(config, 120, 200)
    assert config.save_count == 1

    record_action_point(config, 130, 200)
    assert config.save_count == 2
    assert config.storage['ResourceWatch']['ActionPoint'] == 130


def test_record_yellow_coins_ignores_unusable_readings():
    config = FakeConfig()
    for value in (0, 1, 99):
        record_yellow_coins(config, value)

    assert config.save_count == 0
    assert 'ResourceWatch' not in config.storage

    record_yellow_coins(config, 20200)
    assert config.save_count == 1
    assert config.storage['ResourceWatch']['YellowCoin'] == 20200

    # An OCR miss must not overwrite the last good reading.
    record_yellow_coins(config, 0)
    assert config.storage['ResourceWatch']['YellowCoin'] == 20200
    assert config.save_count == 1


def test_record_keeps_the_other_storage_keys():
    config = FakeConfig(storage={'CoinReplenish': {'reset': 'cycle-1', 'start': 30000}})
    record_action_point(config, 100, 150)

    # The scheduler parks its own breakpoints in the same storage group.
    assert config.storage['CoinReplenish'] == {'reset': 'cycle-1', 'start': 30000}
    assert config.storage['ResourceWatch']['ActionPoint'] == 100


def test_coins_preserve_follows_cl1_when_scheduler_value_is_zero():
    config = FakeConfig(values={
        'OpsiScheduling.OpsiScheduling.OperationCoinsPreserve': 0,
        'OpsiHazard1Leveling.OpsiHazard1Leveling.YellowCoinsPreserve': 10200,
    })
    assert get_coins_preserve(config) == 10200


def test_coins_preserve_uses_the_scheduler_value():
    config = FakeConfig(values={
        'OpsiScheduling.OpsiScheduling.OperationCoinsPreserve': 40000,
        'OpsiHazard1Leveling.OpsiHazard1Leveling.YellowCoinsPreserve': 10200,
    })
    assert get_coins_preserve(config) == 40000


def test_read_dashboard_on_empty_storage():
    config = FakeConfig()
    data = read_dashboard(config)

    assert 'ActionPoint' not in data
    assert 'YellowCoin' not in data
    assert data['CoinPreserve'] == 0


def test_read_dashboard_returns_recorded_values():
    config = FakeConfig(values={
        'OpsiScheduling.OpsiScheduling.OperationCoinsPreserve': 40000,
    })
    record_action_point(config, 95, 195)
    record_yellow_coins(config, 30200)

    data = read_dashboard(config)
    assert data['ActionPoint'] == 95
    assert data['ActionPointTotal'] == 195
    assert data['YellowCoin'] == 30200
    assert data['CoinPreserve'] == 40000
    assert data['Record']


def test_read_dashboard_survives_broken_storage():
    config = FakeConfig()
    # A hand edited config may hold anything in the storage group.
    config.storage = {'ResourceWatch': 'not a dict'}
    data = read_dashboard(config)
    assert data['CoinPreserve'] == 0

    config.storage = {'ResourceWatch': {'ActionPoint': 'abc'}}
    data = read_dashboard(config)
    assert data['ActionPoint'] == 'abc'


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
