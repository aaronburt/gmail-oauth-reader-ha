from unittest.mock import MagicMock
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.gmail_oauth_reader.config_flow import GmailOAuthFlowHandler
from custom_components.gmail_oauth_reader.const import (
    CONF_ENABLE_WRITE,
    CONF_POLL_INTERVAL,
    SCOPE_GMAIL_MODIFY,
    SCOPE_GMAIL_READONLY,
)


async def test_config_flow_user_step_shows_form(hass: HomeAssistant) -> None:
    flow = GmailOAuthFlowHandler()
    flow.hass = hass

    result = await flow.async_step_user(user_input=None)
    assert result['type'] == FlowResultType.FORM
    assert result['step_id'] == 'user'


def test_config_flow_extra_authorize_data() -> None:
    flow = GmailOAuthFlowHandler()
    flow._enable_write = False
    data = flow.extra_authorize_data
    assert SCOPE_GMAIL_READONLY in data['scope']
    assert SCOPE_GMAIL_MODIFY not in data['scope']
    assert 'access_type' in data

    flow._enable_write = True
    data_write = flow.extra_authorize_data
    assert SCOPE_GMAIL_MODIFY in data_write['scope']


async def test_options_flow_init_and_update(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    mock_config_entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    assert result['type'] == FlowResultType.FORM
    assert result['step_id'] == 'init'

    result2 = await hass.config_entries.options.async_configure(
        result['flow_id'],
        user_input={
            CONF_POLL_INTERVAL: 120,
            CONF_ENABLE_WRITE: False,
        },
    )
    assert result2['type'] == FlowResultType.CREATE_ENTRY
    assert mock_config_entry.options[CONF_POLL_INTERVAL] == 120


async def test_options_flow_enable_write_reauth(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    mock_config_entry.add_to_hass(hass)
    mock_config_entry.async_start_reauth = MagicMock()

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    await hass.config_entries.options.async_configure(
        result['flow_id'],
        user_input={
            CONF_POLL_INTERVAL: 60,
            CONF_ENABLE_WRITE: True,
        },
    )
    mock_config_entry.async_start_reauth.assert_called_once_with(hass)
