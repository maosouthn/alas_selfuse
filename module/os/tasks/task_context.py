"""
Task identity context used by the Operation Siren scheduler (OpsiScheduling).

The scheduler owns the whole Operation Siren session and runs sub tasks one
round at a time, without switching tasks and without re-initializing the
session. That requires temporarily running code as another task, while the
scheduler keeps the scheduling ownership.
"""

from contextlib import contextmanager

from module.config.config import Function, name_to_function


_MISSING = object()


@contextmanager
def _temporary_attributes(config, **values):
    """
    Set attributes for the duration of the context and restore them exactly.

    A missing attribute is not the same as an attribute set to None, so track
    the previous value and whether the attribute existed at all.

    Args:
        config (AzurLaneConfig):
        **values: Attributes to set.

    Yields:
        None
    """
    previous = {key: getattr(config, key, _MISSING) for key in values}
    try:
        for key, value in values.items():
            setattr(config, key, value)
        yield
    finally:
        for key, value in previous.items():
            if value is _MISSING:
                if hasattr(config, key):
                    delattr(config, key)
            else:
                setattr(config, key, value)


@contextmanager
def _temporary_config_overrides(config):
    """
    Roll back `config.overridden` and the instance attributes it wrote.

    A proxied sub task may call `config.override()`, which writes both the
    `overridden` dict and plain instance attributes. Both have to be restored,
    otherwise the overrides leak into the scheduler's own decisions.

    Args:
        config (AzurLaneConfig):

    Yields:
        None
    """
    attributes = dict(config.__dict__)
    overridden_before = dict(config.overridden)
    try:
        yield
    finally:
        keys = set(overridden_before)
        current = config.__dict__.get('overridden')
        if isinstance(current, dict):
            keys.update(current)
        # Keep the dict object identity, other code holds a reference to it.
        config.overridden.clear()
        config.overridden.update(overridden_before)
        for key in keys:
            if key in attributes:
                object.__setattr__(config, key, attributes[key])
            elif key in config.__dict__:
                object.__delattr__(config, key)


def task_function(config, task_name):
    """
    Build the Function object of a task from the current config data.

    Args:
        config (AzurLaneConfig):
        task_name (str): Task command, such as 'OpsiHazard1Leveling'.

    Returns:
        Function:
    """
    data = config.data.get(task_name) if isinstance(config.data, dict) else None
    if isinstance(data, dict):
        function = Function(data)
        if function.command != 'Unknown':
            return function
    return name_to_function(task_name)


@contextmanager
def opsi_task_context(config, task_name):
    """
    Run a sub task under its own identity while the scheduler keeps the
    scheduling ownership.

    Inside the context:
    - `config.task` becomes the sub task, so code reading `config.task.command`
      (statistics, drop records, logging, battle timer) still attributes the
      work to the sub task.
    - `_bind_task_override` makes `update()` re-bind the sub task instead of the
      scheduler task.
    - `_task_switch_owner` makes `task_switched()` compare against the scheduler
      task, so the sub task does not mistake the pending scheduler task for a
      preemption and stop itself in the middle of a round.

    Nested proxies keep the outermost owner, the scheduler never loses the
    scheduling ownership to its own sub tasks.

    Args:
        config (AzurLaneConfig):
        task_name (str): Sub task command to impersonate.

    Yields:
        None
    """
    previous_task = config.task
    owner = getattr(config, '_task_switch_owner', None) or previous_task

    with _temporary_config_overrides(config):
        # `config.bind()` writes plain instance attributes, binding itself has
        # to be restored through another `bind()`, not by the attribute restore.
        with _temporary_attributes(
            config,
            task=task_function(config, task_name),
            _bind_task_override=task_name,
            _task_switch_owner=owner,
        ):
            config.bind(task_name)
            try:
                yield
            finally:
                config.bind(previous_task)


def is_proxied(config):
    """
    Check whether the running code is a sub task proxied by the scheduler.

    Args:
        config (AzurLaneConfig):

    Returns:
        bool: True when a scheduler runs this code as one of its sub tasks, which
            is when the current task identity differs from the scheduling owner.
    """
    owner = getattr(config, '_task_switch_owner', None)
    if owner is None:
        return False
    task = getattr(config, 'task', None)
    if task is None:
        return False
    return owner.command != task.command
