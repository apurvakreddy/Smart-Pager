#!/usr/bin/env python3
"""
Hardware Test Script for SmartPager Pi
Tests OLED Display, Buttons, and Accelerometer.
"""
import time
import sys
import RPi.GPIO as GPIO
from smart_display import SmartDisplay, PIN_BUTTON_TOGGLE, PIN_BUTTON_TASK, PIN_BUTTON_SCROLL

def test_display(display):
    print("\n🖥️  Testing Display...")
    print("   Showing 'Display Test'")
    display.show_text("Display Test")
    time.sleep(1)
    print("   Showing 'Hello User'")
    display.show_text("Hello User")
    time.sleep(1)
    print("   Display test complete.")

def test_buttons():
    print("\n🔘 Testing Buttons (Press Ctrl+C to stop)...")
    print("   Please press each button:")
    print(f"   - Toggle (GPIO {PIN_BUTTON_TOGGLE})")
    print(f"   - Task (GPIO {PIN_BUTTON_TASK})")
    print(f"   - Scroll (GPIO {PIN_BUTTON_SCROLL})")
    
    GPIO.setmode(GPIO.BCM)
    buttons = {
        PIN_BUTTON_TOGGLE: "Toggle",
        PIN_BUTTON_TASK: "Task",
        PIN_BUTTON_SCROLL: "Scroll"
    }
    
    for pin in buttons:
        GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        
    try:
        while True:
            for pin, name in buttons.items():
                if GPIO.input(pin) == GPIO.LOW:
                    print(f"   ✅ {name} Button Pressed!")
                    time.sleep(0.2)
            time.sleep(0.05)
    except KeyboardInterrupt:
        print("\n   Button test stopped.")

def test_accel(display):
    print("\n📐 Testing Accelerometer (Lift-to-Wake)...")
    if not display.accel_ok:
        print("   ❌ Accelerometer not detected or failed init.")
        return

    print("   Reading values (Press Ctrl+C to stop)...")
    print("   Try lifting the device to see 'WAKE' detected.")
    
    try:
        while True:
            ax, ay, az = display._read_accel()
            is_lifted = display._is_lifted(ax, ay, az)
            status = "LIFTED ⬆️" if is_lifted else "FLAT ⎯"
            
            sys.stdout.write(f"\r   X={ax:.2f} Y={ay:.2f} Z={az:.2f} | {status}   ")
            sys.stdout.flush()
            
            if is_lifted:
                display.show_text("Lift Detected!")
            
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("\n   Accelerometer test stopped.")

def main():
    print("="*50)
    print("🛠️  SmartPager Hardware Test")
    print("="*50)
    
    try:
        display = SmartDisplay()
    except Exception as e:
        print(f"❌ Failed to init SmartDisplay: {e}")
        return

    while True:
        print("\nSelect test:")
        print("1. Test Display")
        print("2. Test Buttons")
        print("3. Test Accelerometer")
        print("4. Exit")
        
        choice = input("Enter choice (1-4): ")
        
        if choice == '1':
            test_display(display)
        elif choice == '2':
            # Stop display thread briefly to release GPIO or just read parallel?
            # SmartDisplay runs in background, reading GPIO. 
            # We should probably stop it or just read the internal state if we exposed it.
            # But for raw button test, we might conflict.
            # Let's just ask user to watch the log or use the SmartDisplay instance.
            print("   (Note: SmartDisplay is running, buttons will also trigger display actions)")
            test_buttons()
        elif choice == '3':
            test_accel(display)
        elif choice == '4':
            display.cleanup()
            break
        else:
            print("Invalid choice.")

if __name__ == "__main__":
    main()
