"""Stream Deck device control: open, set brightness, push key images."""

from __future__ import annotations

from typing import Optional

from PIL import Image
from StreamDeck.DeviceManager import DeviceManager
from StreamDeck.ImageHelpers import PILHelper


class DeckNotFoundError(RuntimeError):
    pass


class Deck:
    """Thin wrapper around a single Stream Deck device."""

    def __init__(self, brightness: int = 60):
        self._deck = None
        self._brightness = brightness
        self._key_size: Optional[tuple[int, int]] = None

    def open(self) -> "Deck":
        try:
            decks = DeviceManager().enumerate()
        except Exception as exc:  # ProbeError etc. when the HID backend is missing
            raise DeckNotFoundError(
                "Could not access the USB HID backend. Install hidapi "
                "(macOS: `brew install hidapi`, Debian/Ubuntu: "
                "`sudo apt-get install libhidapi-libusb0`). "
                f"Details: {exc}"
            ) from exc
        if not decks:
            raise DeckNotFoundError(
                "No Stream Deck found. Ensure it is plugged in and hidapi is installed."
            )
        self._deck = decks[0]
        self._deck.open()
        self._deck.reset()
        self._deck.set_brightness(self._brightness)
        fmt = self._deck.key_image_format()
        self._key_size = fmt["size"]
        return self

    @property
    def key_count(self) -> int:
        return self._deck.key_count() if self._deck else 0

    @property
    def key_size(self) -> tuple[int, int]:
        return self._key_size or (96, 96)

    @property
    def description(self) -> str:
        if not self._deck:
            return "no device"
        return f"{self._deck.deck_type()} ({self._deck.key_count()} keys)"

    def set_brightness(self, value: int) -> None:
        if self._deck:
            self._deck.set_brightness(max(0, min(100, value)))

    def set_key_image(self, index: int, image: Image.Image) -> None:
        if not self._deck or index < 0 or index >= self._deck.key_count():
            return
        target_w, target_h = self.key_size
        if image.size != (target_w, target_h):
            image = image.resize((target_w, target_h))
        native = PILHelper.to_native_format(self._deck, image)
        self._deck.set_key_image(index, native)

    def render(self, images: dict[int, Image.Image]) -> None:
        for index, image in images.items():
            self.set_key_image(index, image)

    def close(self) -> None:
        if not self._deck:
            return
        try:
            self._deck.reset()
            self._deck.set_brightness(0)
        finally:
            try:
                self._deck.close()
            except Exception:
                pass
            self._deck = None
