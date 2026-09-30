import asyncio
from collections.abc import Mapping
import logging
from typing import Any, cast

import voluptuous as vol

from homeassistant.config_entries import (
    SOURCE_REAUTH,
    ConfigEntry,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import config_entry_oauth2_flow

from .const import (
    CONF_ENABLE_WRITE,
    CONF_EXTRACT_OTP,
    CONF_OTP_EXPIRY_MINUTES,
    CONF_POLL_INTERVAL,
    CONF_QUERY,
    CONF_QUEUE_DWELL_TIME,
    DEFAULT_ENABLE_WRITE,
    DEFAULT_EXTRACT_OTP,
    DEFAULT_OTP_EXPIRY_MINUTES,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_QUERY,
    DEFAULT_QUEUE_DWELL_TIME,
    DOMAIN,
    GMAIL_PROFILE_URL,
    MAX_OTP_EXPIRY_MINUTES,
    MAX_POLL_INTERVAL,
    MAX_QUEUE_DWELL_TIME,
    MIN_OTP_EXPIRY_MINUTES,
    MIN_POLL_INTERVAL,
    MIN_QUEUE_DWELL_TIME,
    SCOPE_GMAIL_SEND,
    SCOPES_READONLY,
    SCOPES_WRITE,
)
from .repairs import async_delete_issue

_LOGGER = logging.getLogger(__name__)


class GmailOAuthFlowHandler(
    config_entry_oauth2_flow.AbstractOAuth2FlowHandler,
    domain=DOMAIN,
):
    DOMAIN = DOMAIN

    def __init__(self) -> None:
        super().__init__()
        self._enable_write: bool = DEFAULT_ENABLE_WRITE

    @property
    def logger(self) -> logging.Logger:
        return _LOGGER

    @property
    def extra_authorize_data(self) -> dict[str, Any]:
        scopes = list(SCOPES_WRITE) if self._enable_write else list(SCOPES_READONLY)
        return {
            "scope": " ".join(scopes),
            "access_type": "offline",
            "prompt": "consent",
        }

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is not None:
            self._enable_write = user_input.get(
                CONF_ENABLE_WRITE, DEFAULT_ENABLE_WRITE
            )
            return await self.async_step_pick_implementation()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_ENABLE_WRITE,
                        default=DEFAULT_ENABLE_WRITE,
                    ): bool,
                }
            ),
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> FlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        reauth_entry = self._get_reauth_entry()
        current_write = reauth_entry.options.get(
            CONF_ENABLE_WRITE,
            reauth_entry.data.get(CONF_ENABLE_WRITE, DEFAULT_ENABLE_WRITE),
        )
        if user_input is not None:
            self._enable_write = user_input.get(
                CONF_ENABLE_WRITE, current_write
            )
            return await self.async_step_pick_implementation()

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_ENABLE_WRITE,
                        default=current_write,
                    ): bool,
                }
            ),
        )

    async def async_oauth_create_entry(self, data: dict[str, Any]) -> FlowResult:
        token = data.get("token", {}).get("access_token")
        headers = {"Authorization": f"Bearer {token}"}
        email_address: str | None = None

        session = config_entry_oauth2_flow.async_get_clientsession(self.hass)
        try:
            async with asyncio.timeout(10):
                async with session.get(GMAIL_PROFILE_URL, headers=headers) as resp:
                    if resp.status == 200:
                        profile = await resp.json()
                        email_address = profile.get("emailAddress")
        except Exception as err:
            _LOGGER.warning("Failed to fetch Gmail profile during OAuth setup: %s", err)

        if not email_address:
            return self.async_abort(reason="oauth_failed")

        await self.async_set_unique_id(email_address)

        if self.source == SOURCE_REAUTH:
            reauth_entry = self._get_reauth_entry()
            self._abort_if_unique_id_mismatch(
                reason="wrong_account",
                description_placeholders={"email": cast(str, reauth_entry.unique_id)},
            )
            async_delete_issue(self.hass, reauth_entry.entry_id)
            data[CONF_ENABLE_WRITE] = self._enable_write
            new_options = dict(reauth_entry.options)
            new_options[CONF_ENABLE_WRITE] = self._enable_write
            return self.async_update_reload_and_abort(
                reauth_entry, data=data, options=new_options
            )

        self._abort_if_unique_id_configured()

        data[CONF_ENABLE_WRITE] = self._enable_write
        return self.async_create_entry(
            title=email_address,
            data=data,
            options={CONF_ENABLE_WRITE: self._enable_write},
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> OptionsFlow:
        return GmailOptionsFlowHandler()


class GmailOptionsFlowHandler(OptionsFlow):
    @property
    def options(self) -> dict[str, Any]:
        return dict(self.config_entry.options)

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        previously_enabled = self.options.get(
            CONF_ENABLE_WRITE,
            self.config_entry.data.get(CONF_ENABLE_WRITE, DEFAULT_ENABLE_WRITE),
        )

        if user_input is not None:
            new_enabled = user_input.get(CONF_ENABLE_WRITE, False)
            if new_enabled and not previously_enabled:
                self.config_entry.async_start_reauth(self.hass)
            return self.async_create_entry(title="", data=user_input)

        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_POLL_INTERVAL,
                    default=self.options.get(
                        CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL
                    ),
                ): vol.All(
                    vol.Coerce(int),
                    vol.Range(min=MIN_POLL_INTERVAL, max=MAX_POLL_INTERVAL),
                ),
                vol.Optional(
                    CONF_QUERY,
                    default=self.options.get(CONF_QUERY, DEFAULT_QUERY),
                ): str,
                vol.Optional(
                    CONF_QUEUE_DWELL_TIME,
                    default=self.options.get(
                        CONF_QUEUE_DWELL_TIME, DEFAULT_QUEUE_DWELL_TIME
                    ),
                ): vol.All(
                    vol.Coerce(int),
                    vol.Range(
                        min=MIN_QUEUE_DWELL_TIME, max=MAX_QUEUE_DWELL_TIME
                    ),
                ),
                vol.Optional(
                    CONF_EXTRACT_OTP,
                    default=self.options.get(
                        CONF_EXTRACT_OTP, DEFAULT_EXTRACT_OTP
                    ),
                ): bool,
                vol.Optional(
                    CONF_OTP_EXPIRY_MINUTES,
                    default=self.options.get(
                        CONF_OTP_EXPIRY_MINUTES, DEFAULT_OTP_EXPIRY_MINUTES
                    ),
                ): vol.All(
                    vol.Coerce(int),
                    vol.Range(
                        min=MIN_OTP_EXPIRY_MINUTES,
                        max=MAX_OTP_EXPIRY_MINUTES,
                    ),
                ),
                vol.Optional(
                    CONF_ENABLE_WRITE,
                    default=previously_enabled,
                ): bool,
            }
        )

        return self.async_show_form(
            step_id="init",
            data_schema=schema,
        )
