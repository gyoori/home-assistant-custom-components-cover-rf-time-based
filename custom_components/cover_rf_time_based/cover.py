"""Cover Time based, RF version."""

import logging

import voluptuous as vol

from datetime import timedelta

from homeassistant.core import callback
from homeassistant.helpers import entity_platform
from homeassistant.helpers.event import async_track_time_interval, async_call_later
from homeassistant.components.cover import (
    ATTR_CURRENT_POSITION,
    ATTR_POSITION,
    PLATFORM_SCHEMA,
    DEVICE_CLASSES_SCHEMA,
    CoverEntity,
)
from homeassistant.const import (
    CONF_NAME,
    CONF_DEVICE_CLASS,
    ATTR_ENTITY_ID,
    ATTR_DEVICE_CLASS,
    SERVICE_CLOSE_COVER,
    SERVICE_OPEN_COVER,
    SERVICE_STOP_COVER,
    STATE_UNAVAILABLE,
)

import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.config_validation import make_entity_service_schema
from .travelcalculator import TravelCalculator
from .travelcalculator import TravelStatus

_LOGGER = logging.getLogger(__name__)

CONF_DEVICES = "devices"
CONF_ALIASES = "aliases"
CONF_TRAVELLING_TIME_DOWN = "travelling_time_down"
CONF_TRAVELLING_TIME_UP = "travelling_time_up"
CONF_SEND_STOP_AT_ENDS = "send_stop_at_ends"
CONF_ALWAYS_CONFIDENT = "always_confident"
CONF_AUTO_CLOSE_SECONDS = "auto_close_seconds"
DEFAULT_TRAVEL_TIME = 25
DEFAULT_SEND_STOP_AT_ENDS = False
DEFAULT_ALWAYS_CONFIDENT = False
DEFAULT_DEVICE_CLASS = "shutter"

CONF_OPEN_SCRIPT_ENTITY_ID = "open_script_entity_id"
CONF_CLOSE_SCRIPT_ENTITY_ID = "close_script_entity_id"
CONF_STOP_SCRIPT_ENTITY_ID = "stop_script_entity_id"
CONF_BUTTON_SCRIPT_ENTITY_ID = "button_script_entity_id"
CONF_COVER_ENTITY_ID = "cover_entity_id"
CONF_AVAILABILITY_TPL = "availability_template"
ATTR_CONFIDENT = "confident"
ATTR_ACTION = "action"
ATTR_POSITION_TYPE = "position_type"
ATTR_POSITION_TYPE_CURRENT = "current"
ATTR_POSITION_TYPE_TARGET = "target"
ATTR_UNCONFIRMED_STATE = "unconfirmed_state"
SERVICE_SET_KNOWN_POSITION = "set_known_position"
SERVICE_SET_KNOWN_ACTION = "set_known_action"

BASE_DEVICE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_NAME): cv.string,
        vol.Optional(CONF_ALIASES, default=[]): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional(
            CONF_TRAVELLING_TIME_DOWN, default=DEFAULT_TRAVEL_TIME
        ): cv.positive_int,
        vol.Optional(
            CONF_TRAVELLING_TIME_UP, default=DEFAULT_TRAVEL_TIME
        ): cv.positive_int,
        vol.Optional(
            CONF_SEND_STOP_AT_ENDS, default=DEFAULT_SEND_STOP_AT_ENDS
        ): cv.boolean,
        vol.Optional(
            CONF_ALWAYS_CONFIDENT, default=DEFAULT_ALWAYS_CONFIDENT
        ): cv.boolean,
        vol.Optional(
            CONF_DEVICE_CLASS, default=DEFAULT_DEVICE_CLASS
        ): DEVICE_CLASSES_SCHEMA,
        vol.Optional(CONF_AVAILABILITY_TPL): cv.template,
        vol.Optional(CONF_AUTO_CLOSE_SECONDS): cv.positive_int,
    }
)

SCRIPT_DEVICE_SCHEMA = BASE_DEVICE_SCHEMA.extend(
    {
        vol.Required(CONF_OPEN_SCRIPT_ENTITY_ID): cv.entity_id,
        vol.Required(CONF_CLOSE_SCRIPT_ENTITY_ID): cv.entity_id,
        vol.Required(CONF_STOP_SCRIPT_ENTITY_ID): cv.entity_id,
    }
)

# Egygombos vezérlő: egyetlen script szolgál nyitásra, zárásra és megállításra is.
BUTTON_DEVICE_SCHEMA = BASE_DEVICE_SCHEMA.extend(
    {
        vol.Required(CONF_BUTTON_SCRIPT_ENTITY_ID): cv.entity_id,
    }
)

COVER_DEVICE_SCHEMA = BASE_DEVICE_SCHEMA.extend(
    {
        vol.Required(CONF_COVER_ENTITY_ID): cv.entity_id,
    }
)

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Optional(CONF_DEVICES, default={}): vol.Schema(
            {cv.string: vol.Any(BUTTON_DEVICE_SCHEMA, SCRIPT_DEVICE_SCHEMA, COVER_DEVICE_SCHEMA)}
        ),
    }
)

POSITION_SCHEMA = make_entity_service_schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_ids,
        vol.Required(ATTR_POSITION): cv.positive_int,
        vol.Optional(ATTR_CONFIDENT, default=False): cv.boolean,
        vol.Optional(ATTR_POSITION_TYPE, default=ATTR_POSITION_TYPE_TARGET): cv.string,
    }
)


ACTION_SCHEMA = make_entity_service_schema(
    {vol.Required(ATTR_ENTITY_ID): cv.entity_ids, vol.Required(ATTR_ACTION): cv.string}
)


DOMAIN = "cover_rf_time_based"


def devices_from_config(domain_config):
    """Parse configuration and add cover devices."""
    devices = []
    for device_id, config in domain_config[CONF_DEVICES].items():
        name = config.pop(CONF_NAME)
        travel_time_down = config.pop(CONF_TRAVELLING_TIME_DOWN)
        travel_time_up = config.pop(CONF_TRAVELLING_TIME_UP)
        open_script_entity_id = config.pop(CONF_OPEN_SCRIPT_ENTITY_ID, None)
        close_script_entity_id = config.pop(CONF_CLOSE_SCRIPT_ENTITY_ID, None)
        stop_script_entity_id = config.pop(CONF_STOP_SCRIPT_ENTITY_ID, None)
        button_script_entity_id = config.pop(CONF_BUTTON_SCRIPT_ENTITY_ID, None)
        if button_script_entity_id is not None:
            open_script_entity_id = button_script_entity_id
            close_script_entity_id = button_script_entity_id
            stop_script_entity_id = button_script_entity_id
        cover_entity_id = config.pop(CONF_COVER_ENTITY_ID, None)
        send_stop_at_ends = config.pop(CONF_SEND_STOP_AT_ENDS)
        always_confident = config.pop(CONF_ALWAYS_CONFIDENT)
        device_class = config.pop(CONF_DEVICE_CLASS)
        availability_template = config.pop(CONF_AVAILABILITY_TPL, None)
        auto_close_seconds = config.pop(CONF_AUTO_CLOSE_SECONDS, None)
        device = CoverTimeBased(
            device_id,
            name,
            travel_time_down,
            travel_time_up,
            open_script_entity_id,
            close_script_entity_id,
            stop_script_entity_id,
            cover_entity_id,
            send_stop_at_ends,
            always_confident,
            device_class,
            availability_template,
            auto_close_seconds,
            button_script_entity_id is not None,
        )
        devices.append(device)
    return devices


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    """Set up the cover platform."""
    async_add_entities(devices_from_config(config))

    platform = entity_platform.current_platform.get()

    platform.async_register_entity_service(
        SERVICE_SET_KNOWN_POSITION, POSITION_SCHEMA, "set_known_position"
    )

    platform.async_register_entity_service(
        SERVICE_SET_KNOWN_ACTION, ACTION_SCHEMA, "set_known_action"
    )


class CoverTimeBased(CoverEntity, RestoreEntity):
    def __init__(
        self,
        device_id,
        name,
        travel_time_down,
        travel_time_up,
        open_script_entity_id,
        close_script_entity_id,
        stop_script_entity_id,
        cover_entity_id,
        send_stop_at_ends,
        always_confident,
        device_class,
        availability_template,
        auto_close_seconds=None,
        single_button=False,
    ):
        """Initialize the cover."""
        self._single_button = single_button  # csak a button_script_entity_id-s (egygombos) kapuknál igaz
        self._auto_close_seconds = auto_close_seconds
        self._unsub_auto_close = None
        self._travel_time_down = travel_time_down
        self._travel_time_up = travel_time_up
        self._open_script_entity_id = open_script_entity_id
        self._close_script_entity_id = close_script_entity_id
        self._stop_script_entity_id = stop_script_entity_id
        self._cover_entity_id = cover_entity_id
        self._send_stop_at_ends = send_stop_at_ends
        self._always_confident = always_confident
        self._device_class = device_class
        self._assume_uncertain_position = not self._always_confident
        self._target_position = 0
        self._processing_known_position = False
        self._last_direction = None  # utolsó fizikai mozgásirány: 'up' / 'down'
        self._unique_id = device_id
        self._availability_template = availability_template

        if name:
            self._name = name
        else:
            self._name = device_id

        self._unsubscribe_auto_updater = None

        self.tc = TravelCalculator(self._travel_time_down, self._travel_time_up)

    async def async_added_to_hass(self):
        """Only cover position and confidence in that matters."""
        """ The rest is calculated from this attribute.        """
        old_state = await self.async_get_last_state()
        _LOGGER.debug(
            self._name + ": " + "async_added_to_hass :: oldState %s", old_state
        )
        if (
            old_state is not None
            and self.tc is not None
            and old_state.attributes.get(ATTR_CURRENT_POSITION) is not None
        ):
            self.tc.set_position(int(old_state.attributes.get(ATTR_CURRENT_POSITION)))
        if (
            old_state is not None
            and old_state.attributes.get(ATTR_UNCONFIRMED_STATE) is not None
            and not self._always_confident
        ):
            if type(old_state.attributes.get(ATTR_UNCONFIRMED_STATE)) == bool:
                self._assume_uncertain_position = old_state.attributes.get(
                    ATTR_UNCONFIRMED_STATE
                )
            else:
                self._assume_uncertain_position = str(
                    old_state.attributes.get(ATTR_UNCONFIRMED_STATE)
                ) == str(True)

    def _cancel_auto_close(self):
        if self._unsub_auto_close is not None:
            self._unsub_auto_close()
            self._unsub_auto_close = None

    def _schedule_auto_close(self):
        """A vezérlő magától zár, ha a kaput megállították: ennyi mp után elindul lefelé (impulzus nélkül)."""
        self._cancel_auto_close()
        if self._auto_close_seconds:
            self._unsub_auto_close = async_call_later(
                self.hass, self._auto_close_seconds, self._auto_close_fired
            )

    async def _auto_close_fired(self, _now):
        self._unsub_auto_close = None
        pos = self.tc.current_position()
        if self.tc.is_traveling() or not (0 < pos < 100):
            return
        _LOGGER.debug(self._name + ": " + "auto close started by controller")
        self.tc.start_travel_down()
        self._target_position = 0
        self._last_direction = "down"
        self.start_auto_updater()
        self.async_write_ha_state()

    def _handle_stop(self):
        """Handle stop button press"""
        if self.tc.is_traveling():
            _LOGGER.debug(self._name + ": " + "_handle_stop :: button stops cover")
            self.tc.stop()
            self.stop_auto_updater()

    @property
    def unconfirmed_state(self):
        """Return the assume state as a string to persist through restarts."""
        return str(self._assume_uncertain_position)

    @property
    def available(self):
        """Return availability based on external template. Always available if no template specified."""
        if self._availability_template is None:
            return True
        else:
            self._availability_template.hass = self.hass
            return self._availability_template.async_render()

    @property
    def name(self):
        """Return the name of the cover."""
        return self._name

    @property
    def unique_id(self):
        """Return the unique id."""
        return "cover_rf_timebased_uuid_" + self._unique_id

    @property
    def device_class(self):
        """Return the device class of the cover."""
        return self._device_class

    @property
    def extra_state_attributes(self):
        """Return the device state attributes."""
        attr = {}
        if self._travel_time_down is not None:
            attr[CONF_TRAVELLING_TIME_DOWN] = self._travel_time_down
        if self._travel_time_up is not None:
            attr[CONF_TRAVELLING_TIME_UP] = self._travel_time_up
        attr[ATTR_UNCONFIRMED_STATE] = str(self._assume_uncertain_position)
        return attr

    @property
    def current_cover_position(self):
        """Return the current position of the cover."""
        return self.tc.current_position()

    @property
    def is_opening(self):
        """Return if the cover is opening or not."""
        return (
            self.tc.is_traveling()
            and self.tc.travel_direction == TravelStatus.DIRECTION_UP
        )

    @property
    def is_closing(self):
        """Return if the cover is closing or not."""
        return (
            self.tc.is_traveling()
            and self.tc.travel_direction == TravelStatus.DIRECTION_DOWN
        )

    @property
    def is_closed(self):
        """Return if the cover is closed."""
        return self.tc.is_closed()

    @property
    def assumed_state(self):
        """Return True unless we have set position with confidence through send_know_position service."""
        return self._assume_uncertain_position

    async def async_set_cover_position(self, **kwargs):
        """Move the cover to a specific position."""
        if ATTR_POSITION in kwargs:
            self._target_position = kwargs[ATTR_POSITION]
            _LOGGER.debug(
                self._name + ": " + "async_set_cover_position: %d",
                self._target_position,
            )
            await self.set_position(self._target_position)

    async def async_close_cover(self, **kwargs):
        """Turn the device close."""
        _LOGGER.debug(self._name + ": " + "async_close_cover")
        if self._single_button:
            await self._async_single_button_move("down")
            return
        # külön fel/le/stop gombos eszköz (redőny): mindig a kért irányba megy
        self.tc.start_travel_down()
        self._target_position = 0
        self.start_auto_updater()
        await self._async_handle_command(SERVICE_CLOSE_COVER)

    async def async_open_cover(self, **kwargs):
        """Turn the device open."""
        _LOGGER.debug(self._name + ": " + "async_open_cover")
        if self._single_button:
            await self._async_single_button_move("up")
            return
        # külön fel/le/stop gombos eszköz (redőny): mindig a kért irányba megy
        self.tc.start_travel_up()
        self._target_position = 100
        self.start_auto_updater()
        await self._async_handle_command(SERVICE_OPEN_COVER)

    async def _async_single_button_move(self, requested):
        """Egygombos vezérlő: minden impulzus léptet (nyit -> megáll -> zár -> megáll).

        - Ha a kapu éppen mozog, az impulzus MEGÁLLÍTJA, függetlenül attól, hogy nyit vagy zár gombot nyomtak.
        - Ha a kapu középen áll, a következő impulzus az utolsó mozgásiránnyal ELLENTÉTES irányba indítja.
        """
        if self.tc.is_traveling():
            await self.async_stop_cover()
            return
        self._cancel_auto_close()
        direction = requested
        pos = self.tc.current_position()
        if self._last_direction is not None and 0 < pos < 100:
            direction = "down" if self._last_direction == "up" else "up"
        self._last_direction = direction
        if direction == "up":
            self.tc.start_travel_up()
            self._target_position = 100
            self.start_auto_updater()
            await self._async_handle_command(SERVICE_OPEN_COVER)
        else:
            self.tc.start_travel_down()
            self._target_position = 0
            self.start_auto_updater()
            await self._async_handle_command(SERVICE_CLOSE_COVER)

    async def async_stop_cover(self, **kwargs):
        """Turn the device stop."""
        _LOGGER.debug(self._name + ": " + "async_stop_cover")
        was_traveling = self.tc.is_traveling()
        self._handle_stop()
        if was_traveling:
            self._schedule_auto_close()
        await self._async_handle_command(SERVICE_STOP_COVER)

    async def set_position(self, position):
        _LOGGER.debug(self._name + ": " + "set_position")
        """Move cover to a designated position."""
        current_position = self.tc.current_position()
        _LOGGER.debug(
            self._name
            + ": "
            + "set_position :: current_position: %d, new_position: %d",
            current_position,
            position,
        )
        command = None
        if position < current_position:
            command = SERVICE_CLOSE_COVER
        elif position > current_position:
            command = SERVICE_OPEN_COVER
        if command is not None:
            self.start_auto_updater()
            self.tc.start_travel(position)
            _LOGGER.debug(self._name + ": " + "set_position :: command %s", command)
            await self._async_handle_command(command)

        return

    def start_auto_updater(self):
        """Start the autoupdater to update HASS while cover is moving."""
        _LOGGER.debug(self._name + ": " + "start_auto_updater")
        if self._unsubscribe_auto_updater is None:
            _LOGGER.debug(self._name + ": " + "init _unsubscribe_auto_updater")
            interval = timedelta(seconds=0.1)
            self._unsubscribe_auto_updater = async_track_time_interval(
                self.hass, self.auto_updater_hook, interval
            )

    @callback
    def auto_updater_hook(self, now):
        """Call for the autoupdater."""
        _LOGGER.debug(self._name + ": " + "auto_updater_hook")
        self.async_schedule_update_ha_state()
        if self.position_reached():
            _LOGGER.debug(self._name + ": " + "auto_updater_hook :: position_reached")
            self.stop_auto_updater()
        self.hass.async_create_task(self.auto_stop_if_necessary())

    def stop_auto_updater(self):
        """Stop the autoupdater."""
        _LOGGER.debug(self._name + ": " + "stop_auto_updater")
        if self._unsubscribe_auto_updater is not None:
            self._unsubscribe_auto_updater()
            self._unsubscribe_auto_updater = None

    def position_reached(self):
        """Return if cover has reached its final position."""
        return self.tc.position_reached()

    async def set_known_action(self, **kwargs):
        """We want to do a few things when we get a position"""
        self._cancel_auto_close()
        action = kwargs[ATTR_ACTION]
        if action not in ["open", "close", "stop"]:
            raise ValueError("action must be one of open, close or cover.")
        if action == "stop":
            self._handle_stop()
            return
        if action == "open":
            self.tc.start_travel_up()
            self._target_position = 100
            self._last_direction = "up"
        if action == "close":
            self.tc.start_travel_down()
            self._target_position = 0
            self._last_direction = "down"
        self.start_auto_updater()

    async def set_known_position(self, **kwargs):
        """We want to do a few things when we get a position"""
        self._cancel_auto_close()
        position = kwargs[ATTR_POSITION]
        confident = kwargs[ATTR_CONFIDENT] if ATTR_CONFIDENT in kwargs else False
        position_type = (
            kwargs[ATTR_POSITION_TYPE]
            if ATTR_POSITION_TYPE in kwargs
            else ATTR_POSITION_TYPE_TARGET
        )
        if position_type not in [ATTR_POSITION_TYPE_TARGET, ATTR_POSITION_TYPE_CURRENT]:
            raise ValueError(
                ATTR_POSITION_TYPE + " must be one of %s, %s",
                ATTR_POSITION_TYPE_TARGET,
                ATTR_POSITION_TYPE_CURRENT,
            )
        _LOGGER.debug(
            self._name
            + ": "
            + "set_known_position :: position  %d, confident %s, position_type %s, self.tc.is_traveling%s",
            position,
            str(confident),
            position_type,
            str(self.tc.is_traveling()),
        )
        self._assume_uncertain_position = (
            not confident if not self._always_confident else False
        )
        self._processing_known_position = True
        if position_type == ATTR_POSITION_TYPE_TARGET:
            self._target_position = position
            position = self.current_cover_position

        if self.tc.is_traveling():
            self.tc.set_position(position)
            self.tc.start_travel(self._target_position)
            self.start_auto_updater()
        else:
            if position_type == ATTR_POSITION_TYPE_TARGET:
                self.tc.start_travel(self._target_position)
                self.start_auto_updater()
            else:
                _LOGGER.debug(
                    self._name
                    + ": "
                    + "set_known_position :: non_traveling position  %d, confident %s, position_type %s",
                    position,
                    str(confident),
                    position_type,
                )
                self.tc.set_position(position)
                if position in (0, 100):
                    self._last_direction = None

    async def auto_stop_if_necessary(self):
        """Do auto stop if necessary."""
        current_position = self.tc.current_position()
        if self.position_reached() and not self._processing_known_position:
            self.tc.stop()
            if (current_position > 0) and (current_position < 100):
                _LOGGER.debug(
                    self._name
                    + ": "
                    + "auto_stop_if_necessary :: current_position between 1 and 99 :: calling stop command"
                )
                await self._async_handle_command(SERVICE_STOP_COVER)
            else:
                if self._send_stop_at_ends:
                    _LOGGER.debug(
                        self._name
                        + ": "
                        + "auto_stop_if_necessary :: send_stop_at_ends :: calling stop command"
                    )
                    await self._async_handle_command(SERVICE_STOP_COVER)

    async def _async_handle_command(self, command, *args):
        """We have cover.* triggered command. Reset assumed state and known_position processsing and execute"""
        self._assume_uncertain_position = not self._always_confident
        self._processing_known_position = False
        cmd = "UNKNOWN"
        if command == "close_cover":
            cmd = "DOWN"
            self._state = False
            if self._cover_entity_id is not None:
                await self.hass.services.async_call(
                    "cover", "close_cover", {"entity_id": self._cover_entity_id}, False
                )
            else:
                await self.hass.services.async_call(
                    "homeassistant",
                    "turn_on",
                    {"entity_id": self._close_script_entity_id},
                    False,
                )

        elif command == "open_cover":
            cmd = "UP"
            self._state = True
            if self._cover_entity_id is not None:
                await self.hass.services.async_call(
                    "cover", "open_cover", {"entity_id": self._cover_entity_id}, False
                )
            else:
                await self.hass.services.async_call(
                    "homeassistant",
                    "turn_on",
                    {"entity_id": self._open_script_entity_id},
                    False,
                )

        elif command == "stop_cover":
            cmd = "STOP"
            self._state = True
            if self._cover_entity_id is not None:
                await self.hass.services.async_call(
                    "cover", "stop_cover", {"entity_id": self._cover_entity_id}, False
                )
            else:
                await self.hass.services.async_call(
                    "homeassistant",
                    "turn_on",
                    {"entity_id": self._stop_script_entity_id},
                    False,
                )

        _LOGGER.debug(self._name + ": " + "_async_handle_command :: %s", cmd)

        # Update state of entity
        self.async_write_ha_state()


# END
