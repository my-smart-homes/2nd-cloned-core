"""Offer API to configure the Home Assistant auth provider."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.auth.providers import homeassistant as auth_ha
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import Unauthorized

import aiohttp
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend
import base64
from . import config_core_secrets as ccs

@callback
def async_setup(hass: HomeAssistant) -> bool:
    """Enable the Home Assistant views."""
    websocket_api.async_register_command(hass, websocket_create)
    websocket_api.async_register_command(hass, websocket_delete)
    websocket_api.async_register_command(hass, websocket_change_password)
    websocket_api.async_register_command(hass, websocket_admin_change_password)
    websocket_api.async_register_command(hass, websocket_admin_change_username)
    return True


@websocket_api.websocket_command(
    {
        vol.Required("type"): "config/auth_provider/homeassistant/create",
        vol.Required("user_id"): str,
        vol.Required("username"): str,
        vol.Required("password"): str,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_create(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Create credentials and attach to a user."""
    provider = auth_ha.async_get_provider(hass)

    if (user := await hass.auth.async_get_user(msg["user_id"])) is None:
        connection.send_error(msg["id"], "not_found", "User not found")
        return

    if user.system_generated:
        connection.send_error(
            msg["id"],
            "system_generated",
            "Cannot add credentials to a system generated user.",
        )
        return

    await provider.async_add_auth(msg["username"], msg["password"])

    credentials = await provider.async_get_or_create_credentials(
        {"username": msg["username"]}
    )
    await hass.auth.async_link_user(user, credentials)

    connection.send_result(msg["id"])


@websocket_api.websocket_command(
    {
        vol.Required("type"): "config/auth_provider/homeassistant/delete",
        vol.Required("username"): str,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_delete(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Delete username and related credential."""
    provider = auth_ha.async_get_provider(hass)
    credentials = await provider.async_get_or_create_credentials(
        {"username": msg["username"]}
    )

    # if not new, an existing credential exists.
    # Removing the credential will also remove the auth.
    if not credentials.is_new:
        await hass.auth.async_remove_credentials(credentials)

        connection.send_result(msg["id"])
        return

    await provider.async_remove_auth(msg["username"])

    connection.send_result(msg["id"])


@websocket_api.websocket_command(
    {
        vol.Required("type"): "config/auth_provider/homeassistant/change_password",
        vol.Required("current_password"): str,
        vol.Required("new_password"): str,
    }
)
@websocket_api.async_response
async def websocket_change_password(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Change current user password."""
    if (user := connection.user) is None:
        connection.send_error(msg["id"], "user_not_found", "User not found")  # type: ignore[unreachable]
        return
    
    if len(msg["new_password"]) < 6:
        connection.send_error(
            msg["id"], "invalid_password", "Password should be at least 6 characters"
        )
        return

    provider = auth_ha.async_get_provider(hass)
    username = None
    for credential in user.credentials:
        if credential.auth_provider_type == provider.type:
            username = credential.data["username"]
            break

    if username is None:
        connection.send_error(
            msg["id"], "credentials_not_found", "Credentials not found"
        )
        return

    try:
        await provider.async_validate_login(username, msg["current_password"])
    except auth_ha.InvalidAuth:
        connection.send_error(
            msg["id"], "invalid_current_password", "Invalid current password"
        )
        return

    await provider.async_change_password(username, msg["new_password"])

    await sync_password_with_firebase(username, msg["current_password"], msg["new_password"])
    
    connection.send_result(msg["id"])


@websocket_api.websocket_command(
    {
        vol.Required(
            "type"
        ): "config/auth_provider/homeassistant/admin_change_password",
        vol.Required("user_id"): str,
        vol.Required("password"): str,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_admin_change_password(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Change password of any user."""
    if not connection.user.is_owner:
        raise Unauthorized(context=connection.context(msg))

    if (user := await hass.auth.async_get_user(msg["user_id"])) is None:
        connection.send_error(msg["id"], "user_not_found", "User not found")
        return

    provider = auth_ha.async_get_provider(hass)

    username = None
    for credential in user.credentials:
        if credential.auth_provider_type == provider.type:
            username = credential.data["username"]
            break

    if username is None:
        connection.send_error(
            msg["id"], "credentials_not_found", "Credentials not found"
        )
        return

    await provider.async_change_password(username, msg["password"])
    connection.send_result(msg["id"])


@websocket_api.websocket_command(
    {
        vol.Required(
            "type"
        ): "config/auth_provider/homeassistant/admin_change_username",
        vol.Required("user_id"): str,
        vol.Required("username"): str,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_admin_change_username(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Change the username for any user."""
    if not connection.user.is_owner:
        raise Unauthorized(context=connection.context(msg))

    if (user := await hass.auth.async_get_user(msg["user_id"])) is None:
        connection.send_error(msg["id"], "user_not_found", "User not found")
        return

    provider = auth_ha.async_get_provider(hass)
    found_credential = None
    for credential in user.credentials:
        if credential.auth_provider_type == provider.type:
            found_credential = credential
            break

    if found_credential is None:
        connection.send_error(
            msg["id"], "credentials_not_found", "Credentials not found"
        )
        return

    await provider.async_change_username(found_credential, msg["username"])
    connection.send_result(msg["id"])


async def sync_password_with_firebase(email: str, current_password: str, new_password: str) -> None:
    """Sync password change with Firebase asynchronously."""

    key = ccs.AES_ENC_KEY
    iv = ccs.AES_ENC_IV

    # Check lengths (for verification purposes)
    assert len(key) == 32, "Key must be 32 bytes for AES-256."
    assert len(iv) == 16, "IV must be 16 bytes for AES-CBC."

    # Data to encrypt
    data = new_password.encode('utf-8')

    # Pad data to AES block size (128 bits for AES)
    padder = padding.PKCS7(128).padder()
    padded_data = padder.update(data) + padder.finalize()

    # Encrypt with AES-256-CBC using constant key and IV
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
    encryptor = cipher.encryptor()
    encrypted_data = encryptor.update(padded_data) + encryptor.finalize()

    # Encode the encrypted data to Base64
    encrypted_base64 = base64.b64encode(encrypted_data).decode('utf-8')

    url = 'https://updateuserpassword-jrskleaqea-uc.a.run.app'
    headers = {'Content-Type': 'application/json'}
    data = {
        "email": email,
        "currentPassword": current_password,
        "newPassword": encrypted_base64
    }
    
    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=data) as response:
            if response.status != 200:
                # Log or handle error as needed
                print("Failed to sync password with Firebase:", await response.text())
            else:
                print("Password synced with Firebase successfully.")

