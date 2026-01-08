#!/usr/bin/env python3
import time
import math
import threading
from datetime import datetime, timedelta
from typing import Optional, List, Tuple, Dict

import RPi.GPIO as GPIO
from luma.core.interface.serial import i2c
from luma.core.render import canvas
from luma.oled.device import ssd1306

# Try smbus2 first, fall back to smbus
try:
    import smbus2 as smbus
except ImportError:
    import smbus

# ==================== CONFIGURATION ====================

# I2C Configuration
I2C_PORT = 1
I2C_ADDRESS_OLED = 0x3C
I2C_ADDRESS_ACCEL = 0x6A

# GPIO Configuration (BCM)
# Button Mapping:
# A (GPIO 4): Next Day
# B (GPIO 5): Page Down / Next Window
# C (GPIO 6): Toggle View (Summary <-> List)
PIN_BUTTON_A = 4   # Was TOGGLE
PIN_BUTTON_B = 5   # Was TASK
PIN_BUTTON_C = 6   # Was SCROLL

# Accelerometer Registers (ISM330DLC)
REG_WHO_AM_I  = 0x0F
REG_CTRL1_XL  = 0x10
REG_CTRL3_C   = 0x12
REG_OUTX_L_XL = 0x28
ACC_SENSITIVITY_2G = 0.061 / 1000.0

# Display Settings
DISPLAY_WIDTH = 128
DISPLAY_HEIGHT = 32
ROW_HEIGHT = 10
VISIBLE_ROWS = DISPLAY_HEIGHT // ROW_HEIGHT

DAYS_OF_WEEK = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']

class SmartDisplay:
    def __init__(self):
        self.running = True
        self.display_on = True
        self.scroll_mode = False # False = Summary View, True = List View
        self.scroll_lines = []
        self.scroll_top_index = 0
        
        # Data Storage
        self.week_data = {} # { 'monday': {events: []}, ... }
        self.current_day_idx = datetime.now().weekday() # 0=Monday, 6=Sunday
        self.last_update_time = datetime.now()
        
        self.current_message = "SmartPager Ready"
        
        # Initialize Hardware
        self._init_gpio()
        self._init_i2c()
        self._init_display()
        self._init_accel()
        
        # Start background thread for sensors and buttons
        self.thread = threading.Thread(target=self._hardware_loop, daemon=True)
        self.thread.start()
        
        # Initial draw
        self.update_display()

    def _init_gpio(self):
        GPIO.setmode(GPIO.BCM)
        buttons = [PIN_BUTTON_A, PIN_BUTTON_B, PIN_BUTTON_C]
        for pin in buttons:
            GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
            
        self.button_states = {pin: GPIO.input(pin) for pin in buttons}

    def _init_i2c(self):
        self.bus = smbus.SMBus(I2C_PORT)

    def _init_display(self):
        try:
            serial = i2c(port=I2C_PORT, address=I2C_ADDRESS_OLED)
            self.device = ssd1306(serial, width=DISPLAY_WIDTH, height=DISPLAY_HEIGHT)
            self.device.show()
        except Exception as e:
            print(f"⚠️ Display init failed: {e}")
            self.device = None

    def _init_accel(self):
        try:
            # Check WHO_AM_I
            who = self.bus.read_byte_data(I2C_ADDRESS_ACCEL, REG_WHO_AM_I)
            if who not in [0x6A, 0x6B]:
                print(f"⚠️ Accel WHO_AM_I = 0x{who:02X}, expected 0x6A or 0x6B")
                
            # Init config
            # CTRL3_C: BDU=1
            self.bus.write_byte_data(I2C_ADDRESS_ACCEL, REG_CTRL3_C, 0x40)
            # CTRL1_XL: 52Hz, 2g
            self.bus.write_byte_data(I2C_ADDRESS_ACCEL, REG_CTRL1_XL, 0x30)
            self.accel_ok = True
        except Exception as e:
            print(f"⚠️ Accel init failed: {e}")
            self.accel_ok = False

    def _read_accel(self):
        if not self.accel_ok:
            return 0, 0, 0
            
        try:
            data = self.bus.read_i2c_block_data(I2C_ADDRESS_ACCEL, REG_OUTX_L_XL, 6)
            
            def twos_comp(val, bits=16):
                if val & (1 << (bits - 1)):
                    val -= 1 << bits
                return val

            x = twos_comp(data[1] << 8 | data[0]) * ACC_SENSITIVITY_2G
            y = twos_comp(data[3] << 8 | data[2]) * ACC_SENSITIVITY_2G
            z = twos_comp(data[5] << 8 | data[4]) * ACC_SENSITIVITY_2G
            return x, y, z
        except:
            return 0, 0, 0

    def _is_lifted(self, ax, ay, az):
        g_total = math.sqrt(ax*ax + ay*ay + az*az)
        if g_total < 0.7 or g_total > 1.3:
            return False
            
        # Vertical if Z is small and X or Y is large
        vertical_component = max(abs(ax), abs(ay))
        return abs(az) < 0.5 and vertical_component > 0.7

    def _hardware_loop(self):
        was_vertical = False
        
        while self.running:
            # 1. Poll Accelerometer
            if self.accel_ok:
                ax, ay, az = self._read_accel()
                is_vertical = self._is_lifted(ax, ay, az)
                
                if is_vertical and not was_vertical:
                    self.wake()
                was_vertical = is_vertical

            # 2. Poll Buttons
            for pin in self.button_states:
                current = GPIO.input(pin)
                if self.button_states[pin] == GPIO.HIGH and current == GPIO.LOW:
                    # Button Pressed
                    self._handle_button(pin)
                self.button_states[pin] = current

            time.sleep(0.05)

    def _handle_button(self, pin):
        self.wake()
        
        if pin == PIN_BUTTON_A:
            # Button A: Next Day
            self.current_day_idx = (self.current_day_idx + 1) % 7
            day_name = DAYS_OF_WEEK[self.current_day_idx].capitalize()
            # Reset scroll
            self.scroll_top_index = 0
            self._prepare_scroll_lines()
            # Briefly show day name if in summary mode?
            # Actually update_display will handle showing the day
            self.update_display()
            
        elif pin == PIN_BUTTON_B:
            # Button B: Next Window / Page Down
            if self.scroll_mode:
                max_top = max(0, len(self.scroll_lines) - VISIBLE_ROWS)
                # Scroll by one page (window)
                new_top = self.scroll_top_index + (VISIBLE_ROWS - 1)
                if new_top > max_top:
                    new_top = 0 # Loop back to top? Or just stop?
                    # Let's loop back to top for easy cycling
                self.scroll_top_index = new_top
            else:
                # In summary mode, maybe switch to list mode?
                self.scroll_mode = True
                self._prepare_scroll_lines()
            self.update_display()
            
        elif pin == PIN_BUTTON_C:
            # Button C: Toggle View
            self.scroll_mode = not self.scroll_mode
            if self.scroll_mode:
                self._prepare_scroll_lines()
            self.update_display()

    def wake(self):
        if not self.display_on:
            self.display_on = True
            if self.device:
                self.device.show()
            self.update_display()

    def sleep(self):
        if self.display_on:
            self.display_on = False
            if self.device:
                self.device.hide()

    def show_text(self, text: str):
        self.current_message = text
        self.scroll_mode = False
        self.wake()
        self.update_display()

    def update_week_schedule(self, week_data: Dict):
        """Update the full week's schedule data."""
        if 'days' in week_data:
            self.week_data = week_data['days']
        else:
            self.week_data = week_data
            
        # Auto-select today
        self.current_day_idx = datetime.now().weekday()
        self._prepare_scroll_lines()
        self.update_display()

    def update_schedule(self, events: List[Dict]):
        """Legacy support: update just the current day or infer."""
        # If we get a simple list, assume it's for 'today' or the current view
        day_name = DAYS_OF_WEEK[self.current_day_idx]
        if day_name not in self.week_data:
            self.week_data[day_name] = {}
        self.week_data[day_name]['events'] = events
        self._prepare_scroll_lines()
        self.update_display()

    def _get_current_day_events(self):
        day_name = DAYS_OF_WEEK[self.current_day_idx]
        day_data = self.week_data.get(day_name, {})
        return day_data.get('events', [])

    def _prepare_scroll_lines(self):
        events = self._get_current_day_events()
        lines = []
        
        # Header line: Day Name
        day_name = DAYS_OF_WEEK[self.current_day_idx].capitalize()
        lines.append(f"[{day_name}]")
        
        if not events:
            lines.append("No events")
        else:
            # Sort events
            parsed_events = []
            for e in events:
                try:
                    start = datetime.fromisoformat(e['start'])
                    parsed_events.append((start, e))
                except:
                    continue
            parsed_events.sort()
            
            now = datetime.now()
            # Determine "next" event for the arrow
            # If viewing today, next is first future event
            # If viewing future day, next is first event
            # If viewing past day, no next?
            
            viewing_today = (self.current_day_idx == now.weekday())
            found_next = False
            
            for start, e in parsed_events:
                time_str = start.strftime('%-I%p').lower() # 5pm
                # Adjust format: "5pm Event"
                name = e.get('name', 'Event')
                line_content = f"{time_str} {name}"
                
                prefix = "  "
                if viewing_today:
                    if not found_next and start > now:
                        prefix = "> "
                        found_next = True
                elif start.date() > now.date():
                     # Future day: first event is next
                     if not found_next:
                         prefix = "> "
                         found_next = True
                
                lines.append(f"{prefix}{line_content}")
                
        self.scroll_lines = lines
        self.scroll_top_index = 0

    def update_display(self):
        if not self.device or not self.display_on:
            return

        with canvas(self.device) as draw:
            if self.scroll_mode:
                # Draw scroll list
                for i in range(VISIBLE_ROWS):
                    idx = self.scroll_top_index + i
                    if idx < len(self.scroll_lines):
                        draw.text((0, i * ROW_HEIGHT), self.scroll_lines[idx], fill=255)
            else:
                # Summary View
                day_name = DAYS_OF_WEEK[self.current_day_idx].capitalize()
                events = self._get_current_day_events()
                
                # Format:
                # DayName
                # Next: Event @ Time
                
                draw.text((0, 0), day_name, fill=255)
                
                next_text = self._format_next_event_summary(events)
                # Wrap text if needed, or just show first line
                draw.text((0, ROW_HEIGHT), next_text, fill=255)

    def _format_next_event_summary(self, events):
        if not events:
            return "No events"
            
        now = datetime.now()
        viewing_today = (self.current_day_idx == now.weekday())
        
        parsed_events = []
        for e in events:
            try:
                start = datetime.fromisoformat(e['start'])
                parsed_events.append((start, e))
            except:
                continue
        parsed_events.sort()
        
        for start, e in parsed_events:
            if viewing_today:
                if start > now:
                    return f"{start.strftime('%-I:%M%p').lower()} {e['name']}"
            else:
                # Just show the first event of the day
                return f"{start.strftime('%-I:%M%p').lower()} {e['name']}"
        
        return "No more events"

    def cleanup(self):
        self.running = False
        if self.thread.is_alive():
            self.thread.join(timeout=1.0)
