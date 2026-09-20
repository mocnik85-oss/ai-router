from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import Any

from dbus_next.aio import MessageBus
from dbus_next.constants import BusType


class CredentialError(Exception):
    """Base class for credential-store errors."""


class CredentialNotFoundError(CredentialError):
    """Raised when a requested provider credential does not exist."""


class CredentialStore(ABC):
    """Provider-independent credential storage interface."""

    @abstractmethod
    def get(self, provider: str) -> str:
        """Return the credential for a provider."""

    @abstractmethod
    def set(self, provider: str, credential: str) -> None:
        """Store a credential for a provider."""

    @abstractmethod
    def delete(self, provider: str) -> None:
        """Delete a provider credential."""


class MemoryCredentialStore(CredentialStore):
    """Non-persistent credential store used by tests."""

    def __init__(self) -> None:
        self._credentials: dict[str, str] = {}

    def get(self, provider: str) -> str:
        try:
            return self._credentials[provider]
        except KeyError as exc:
            raise CredentialNotFoundError(
                f"No credential configured for provider: {provider}"
            ) from exc

    def set(self, provider: str, credential: str) -> None:
        if not provider:
            raise CredentialError("Provider name cannot be empty.")

        if not credential:
            raise CredentialError("Credential cannot be empty.")

        self._credentials[provider] = credential

    def delete(self, provider: str) -> None:
        self._credentials.pop(provider, None)


class KWalletCredentialStore(CredentialStore):
    """
    KWallet-backed credential store.

    Uses the user's KDE session bus and the kdewallet wallet.

    Credentials are stored in the dedicated ai-router folder.
    """

    SERVICE = "org.kde.kwalletd6"
    OBJECT = "/modules/kwalletd6"
    INTERFACE = "org.kde.KWallet"

    WALLET = "kdewallet"
    FOLDER = "ai-router"
    APP_ID = "ai-router"

    def _run(self, coroutine: Any) -> Any:
        return asyncio.run(coroutine)

    async def _open_wallet(self) -> tuple[MessageBus, Any, int]:
        bus = MessageBus(bus_type=BusType.SESSION)

        try:
            await bus.connect()

            introspection = await bus.introspect(
                self.SERVICE,
                self.OBJECT,
            )

            proxy = bus.get_proxy_object(
                self.SERVICE,
                self.OBJECT,
                introspection,
            )

            interface = proxy.get_interface(self.INTERFACE)

            handle = await interface.call_open(
                self.WALLET,
                0,
                self.APP_ID,
            )

            if handle < 0:
                bus.disconnect()
                raise CredentialError(
                    "Unable to open KWallet."
                )

            return bus, interface, handle

        except CredentialError:
            raise
        except Exception as exc:
            bus.disconnect()
            raise CredentialError(
                "Unable to connect to KWallet."
            ) from exc

    async def _get(self, provider: str) -> str:
        bus, interface, handle = await self._open_wallet()

        try:
            exists = await interface.call_has_folder(
                handle,
                self.FOLDER,
                self.APP_ID,
            )

            if not exists:
                created = await interface.call_create_folder(
                    handle,
                    self.FOLDER,
                    self.APP_ID,
                )

                if not created:
                    raise CredentialError(
                        "Unable to create the ai-router KWallet folder."
                    )

            exists = await interface.call_has_entry(
                handle,
                self.FOLDER,
                provider,
                self.APP_ID,
            )

            if not exists:
                raise CredentialNotFoundError(
                    f"No credential configured for provider: {provider}"
                )

            value = await interface.call_read_password(
                handle,
                self.FOLDER,
                provider,
                self.APP_ID,
            )

            if not value:
                raise CredentialNotFoundError(
                    f"No credential configured for provider: {provider}"
                )

            return value

        finally:
            try:
                await interface.call_close(
                    handle,
                    False,
                    self.APP_ID,
                )
            finally:
                bus.disconnect()

    async def _set(self, provider: str, credential: str) -> None:
        bus, interface, handle = await self._open_wallet()

        try:
            exists = await interface.call_has_folder(
                handle,
                self.FOLDER,
                self.APP_ID,
            )

            if not exists:
                created = await interface.call_create_folder(
                    handle,
                    self.FOLDER,
                    self.APP_ID,
                )

                if not created:
                    raise CredentialError(
                        "Unable to create the ai-router KWallet folder."
                    )

            result = await interface.call_write_password(
                handle,
                self.FOLDER,
                provider,
                credential,
                self.APP_ID,
            )

            if result != 0:
                raise CredentialError(
                    "KWallet rejected the credential."
                )

        finally:
            try:
                await interface.call_close(
                    handle,
                    False,
                    self.APP_ID,
                )
            finally:
                bus.disconnect()

    def get(self, provider: str) -> str:
        if not provider:
            raise CredentialError("Provider name cannot be empty.")

        return self._run(self._get(provider))

    def set(self, provider: str, credential: str) -> None:
        if not provider:
            raise CredentialError("Provider name cannot be empty.")

        if not credential:
            raise CredentialError("Credential cannot be empty.")

        self._run(self._set(provider, credential))

    def delete(self, provider: str) -> None:
        """Delete a provider credential from KWallet."""

        async def _delete() -> None:
            bus, interface, handle = await self._open_wallet()

            try:
                result = await interface.call_remove_entry(
                    handle,
                    self.FOLDER,
                    provider,
                    self.APP_ID,
                )

                if result != 0:
                    raise CredentialError(
                        f"KWallet removeEntry failed with code {result}."
                    )
            finally:
                try:
                    await interface.call_close(
                        handle,
                        False,
                        self.APP_ID,
                    )
                finally:
                    bus.disconnect()

        self._run(_delete())
