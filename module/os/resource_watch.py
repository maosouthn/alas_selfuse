"""
Resource watch for the Operation Siren dashboard.

Action points and yellow coins are read all the time while Operation Siren is
running, but only inside the game session. The readings are parked in the
existing `Storage` group of OpsiScheduling so the WebUI can show them, which
keeps the feature free of any new config argument: `Storage` is appended to
every task by the config generator, so `config_generated.py`, `args.json`,
`menu.json` and `template.json` stay untouched.
"""

from datetime import datetime

from module.logger import logger

# Reuse the Storage group OpsiScheduling already has, no new config argument.
CONFIG_PATH_STORAGE = 'OpsiScheduling.Storage.Storage'
STATE_KEY = 'ResourceWatch'
# The shop sells 20200 yellow coins worth at most in one purchase, anything
# below this is an OCR reading taken before the number finished loading.
MIN_VALID_YELLOW_COINS = 100
# Yellow coins preserve, the scheduler value wins and 0 follows hazard 1 leveling.
CONFIG_PATH_COINS_PRESERVE = 'OpsiScheduling.OpsiScheduling.OperationCoinsPreserve'
CONFIG_PATH_COINS_PRESERVE_FALLBACK = 'OpsiHazard1Leveling.OpsiHazard1Leveling.YellowCoinsPreserve'


def _as_int(value):
    """
    Args:
        value: Raw config value.

    Returns:
        int | None: The value as int, None when it is not a usable number.
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _read_storage(config):
    """
    Args:
        config (AzurLaneConfig):

    Returns:
        tuple[dict, dict]: The whole storage state and the resource watch part.
    """
    value = config.cross_get(keys=CONFIG_PATH_STORAGE, default=None)
    state = dict(value) if isinstance(value, dict) else {}
    watch = state.get(STATE_KEY)
    return state, (dict(watch) if isinstance(watch, dict) else {})


def _record(config, values):
    """
    Store readings, writing the config only when something actually changed.

    `action_point_update()` runs on every screenshot while the action point
    popup is open, so most calls repeat the same numbers and must not touch the
    config file.

    Args:
        config (AzurLaneConfig):
        values (dict): Field name to reading.
    """
    state, watch = _read_storage(config)
    changed = False
    for key, value in values.items():
        value = _as_int(value)
        if value is None or watch.get(key) == value:
            continue
        watch[key] = value
        changed = True
    if not changed:
        return

    watch['Record'] = datetime.now().replace(microsecond=0).isoformat(sep=' ')
    state[STATE_KEY] = watch
    config.cross_set(keys=CONFIG_PATH_STORAGE, value=state)
    config.save()


def record_action_point(config, current, total):
    """
    Record one action point reading.

    Args:
        config (AzurLaneConfig):
        current (int): Current action points.
        total (int): Total action points including the boxes.
    """
    try:
        _record(config, {'ActionPoint': current, 'ActionPointTotal': total})
    except Exception:
        logger.exception('[Resource watch] Failed to record action points')


def record_yellow_coins(config, coins):
    """
    Record one yellow coins reading.

    A reading below MIN_VALID_YELLOW_COINS is either an OCR miss or a number
    which has not finished loading, it must not overwrite the last good value.

    Args:
        config (AzurLaneConfig):
        coins (int): Yellow coins, the Operation Siren supply voucher.
    """
    value = _as_int(coins)
    if value is None or value < MIN_VALID_YELLOW_COINS:
        return
    try:
        _record(config, {'YellowCoin': value})
    except Exception:
        logger.exception('[Resource watch] Failed to record yellow coins')


def get_coins_preserve(config):
    """
    Yellow coins preserve, the same thresholds the scheduler replenishes around.

    Args:
        config (AzurLaneConfig):

    Returns:
        int: Preserve, 0 when nothing is configured.
    """
    for key in (CONFIG_PATH_COINS_PRESERVE, CONFIG_PATH_COINS_PRESERVE_FALLBACK):
        value = _as_int(config.cross_get(keys=key, default=None))
        if value:
            return value
    return 0


def read_dashboard(config):
    """
    Read the readings for the dashboard.

    Args:
        config (AzurLaneConfig):

    Returns:
        dict: ActionPoint, ActionPointTotal, YellowCoin, Record and CoinPreserve.
            Missing readings are simply absent from the dict.
    """
    _, watch = _read_storage(config)
    watch['CoinPreserve'] = get_coins_preserve(config)
    return watch
