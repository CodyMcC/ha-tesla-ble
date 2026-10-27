"""Media player entity backed by BLE media_state polls."""

from __future__ import annotations

from homeassistant.components.media_player import (
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import TeslaBLEConfigEntry
from .entity import TeslaBLEEntity
from .manager import TeslaBLEManager

# CarServer MediaPlaybackStatus
_PLAYBACK_STOPPED = 0
_PLAYBACK_PLAYING = 1
_PLAYBACK_PAUSED = 2

# vehicle-command SetVolume uses 0-10.
_VOLUME_MAX = 10.0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TeslaBLEConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    manager: TeslaBLEManager = entry.runtime_data
    if not manager.can_control_vehicle:
        return
    async_add_entities([TeslaMediaPlayer(manager)])


class TeslaMediaPlayer(TeslaBLEEntity, MediaPlayerEntity):
    _attr_translation_key = "media"
    _attr_supported_features = (
        MediaPlayerEntityFeature.PAUSE
        | MediaPlayerEntityFeature.PLAY
        | MediaPlayerEntityFeature.NEXT_TRACK
        | MediaPlayerEntityFeature.PREVIOUS_TRACK
        | MediaPlayerEntityFeature.VOLUME_SET
        | MediaPlayerEntityFeature.VOLUME_STEP
        | MediaPlayerEntityFeature.STOP
    )

    def __init__(self, manager: TeslaBLEManager) -> None:
        super().__init__(manager, "media_player_media", "media_state")

    @property
    def available(self) -> bool:
        return super().available and self.manager.reachable and not self.manager._key_rejected

    @property
    def state(self) -> MediaPlayerState | None:
        status = self.manager.media_state.get("media_playback_status")
        if status is None:
            return None
        if status == _PLAYBACK_PLAYING:
            return MediaPlayerState.PLAYING
        if status == _PLAYBACK_PAUSED:
            return MediaPlayerState.PAUSED
        return MediaPlayerState.IDLE

    @property
    def media_title(self) -> str | None:
        return self.manager.media_state.get("now_playing_title")

    @property
    def media_artist(self) -> str | None:
        return self.manager.media_state.get("now_playing_artist")

    @property
    def volume_level(self) -> float | None:
        volume = self.manager.media_state.get("audio_volume")
        if volume is None:
            return None
        maximum = self.manager.media_state.get("audio_volume_max") or _VOLUME_MAX
        if maximum <= 0:
            return None
        return max(0.0, min(1.0, float(volume) / float(maximum)))

    async def async_media_play(self) -> None:
        if self.state != MediaPlayerState.PLAYING:
            await self.manager.async_media_toggle_playback()

    async def async_media_pause(self) -> None:
        if self.state == MediaPlayerState.PLAYING:
            await self.manager.async_media_toggle_playback()

    async def async_media_stop(self) -> None:
        if self.state == MediaPlayerState.PLAYING:
            await self.manager.async_media_toggle_playback()

    async def async_media_play_pause(self) -> None:
        await self.manager.async_media_toggle_playback()

    async def async_media_next_track(self) -> None:
        await self.manager.async_media_next_track()

    async def async_media_previous_track(self) -> None:
        await self.manager.async_media_previous_track()

    async def async_set_volume_level(self, volume: float) -> None:
        maximum = self.manager.media_state.get("audio_volume_max") or _VOLUME_MAX
        await self.manager.async_set_media_volume(max(0.0, min(float(maximum), volume * float(maximum))))

    async def async_volume_up(self) -> None:
        await self.manager.async_media_volume_up()

    async def async_volume_down(self) -> None:
        await self.manager.async_media_volume_down()
