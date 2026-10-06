"""
Resource dashboard of the Overview page.

Shows the last action points and yellow coins readings taken from the game, so
the Operation Siren loop can be watched without opening the game: leveling
burns action points, replenishing buys them back with yellow coins.
"""

from pywebio.output import clear, put_column, put_text, use_scope

from module.os.resource_watch import read_dashboard
from module.webui.lang import t


def _put_reading(label, value):
    """
    Render one label and reading pair on a single row.

    Args:
        label (str): Row label.
        value (str): Formatted reading.
    """
    put_column(
        [
            put_text(label).style("--arg-title--"),
            put_text(value).style("--arg-help--"),
        ],
        size="auto auto",
    )


def put_resource_dashboard(config):
    """
    Render the resource readings into the `resource` scope of the Overview page.

    Args:
        config (AzurLaneConfig): Config of the WebUI side.
    """
    clear("resource")
    with use_scope("resource"):
        data = read_dashboard(config)

        current = data.get('ActionPoint')
        total = data.get('ActionPointTotal')
        if current is None:
            action_point = '--'
        elif total:
            action_point = f'{current} / {total}'
        else:
            action_point = str(current)

        coins = data.get('YellowCoin')
        if coins is None:
            yellow_coin = '--'
        else:
            yellow_coin = f'{coins:,}'
            preserve = data.get('CoinPreserve') or 0
            if preserve:
                yellow_coin = f'{yellow_coin} ({t("Gui.Overview.CoinPreserve")} {preserve:,})'

        put_text(t('Gui.Overview.Resource')).style("--arg-title--")
        _put_reading(t('Gui.Overview.ActionPoint'), action_point)
        _put_reading(t('Gui.Overview.YellowCoin'), yellow_coin)

        record = data.get('Record')
        if record:
            put_text(f'{t("Gui.Overview.UpdatedAt")} {record}').style("--arg-help--")
        else:
            put_text(t('Gui.Overview.NoData')).style("--arg-help--")
