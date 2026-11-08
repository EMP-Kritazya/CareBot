# CareBot wiring (Raspberry Pi 5)

Owned by Hardware. The final pin map and power notes. Add a wiring photo once the robot is built.
Physical pin numbers: pin 1 is at the end of the header farthest from the USB and Ethernet ports;
odd pins are the inner row, even pins the outer row. `pinout` on the Pi prints the map.

## Breadboard rails

Run three wires from the Pi to the breadboard rails, and connect the small parts to the rails:

| Rail      | From the Pi      | Feeds                                          |
| --------- | ---------------- | ---------------------------------------------- |
| 3.3 V     | pin 1            | mic VDD, level shifter LV, Grove modules       |
| 5 V       | pin 4            | LED eyes, level shifter HV, buzzer (+)         |
| GND       | pin 6            | every ground on the breadboard                 |

Servo power never touches these rails (see below).

## Full pin map

| Part                       | Wire                  | Pi pin            | Notes                                       |
| -------------------------- | --------------------- | ----------------- | ------------------------------------------- |
| Pan servo                  | yellow (signal)       | 32 (GPIO 12)      | hardware PWM0 channel 0                     |
| Tilt servo                 | yellow (signal)       | 33 (GPIO 13)      | hardware PWM0 channel 1                     |
| Servo battery minus        | wire to the Pi        | 34 (GND)          | shared ground, required                     |
| Button, plain              | one leg / other leg   | 13 (GPIO 27) / 14 (GND) | `button.pull_up: true`                |
| Button, Grove module       | SIG / VCC / GND       | 13 / 3.3 V rail / GND rail | `button.pull_up: false`            |
| Buzzer, 3-pin module       | S (I/O) / VCC / GND   | 11 (GPIO 17) / 3.3 V rail / GND rail | straight to the pin      |
| Buzzer, bare 2-leg         | through a 2N3904      | 11 (GPIO 17)      | see "Bare buzzer" below                     |
| LED eyes (WS2812B)         | DIN                   | 19 (GPIO 10)      | through the level shifter (see below)       |
| LED eyes                   | 5V / GND              | 5 V rail / GND rail |                                           |
| INMP441 mic                | SCK                   | 12 (GPIO 18)      | I2S clock                                   |
| INMP441 mic                | WS                    | 35 (GPIO 19)      | I2S word select                             |
| INMP441 mic                | SD                    | 38 (GPIO 20)      | I2S data in                                 |
| INMP441 mic                | VDD / GND / L/R       | 3.3 V rail / GND rail / GND rail | never 5 V; L/R to GND = left channel |
| Webcam (Logitech)          | USB                   | blue USB 3 port   | 1280x720 MJPG, see config `camera:`         |

Leave pins 27 and 28 (ID_SD, ID_SC) free.

## Pan-tilt head: two Hitec D625MW servos

The servos changed from the planned SG90s to Hitec D625MW (4.8 to 7.4 V, up to 2.65 A each at
stall), so they run from their own battery, not the Pi.

| Servo wire      | Pan servo              | Tilt servo             |
| --------------- | ---------------------- | ---------------------- |
| Yellow (signal) | Pi pin 32, GPIO 12     | Pi pin 33, GPIO 13     |
| Red (+)         | battery +              | battery +              |
| Black (GND)     | battery minus          | battery minus          |

- One wire from battery minus to **Pi pin 34 (GND)**. Without this shared ground the servos can't read
  the signal.
- **Nothing** from the battery goes to the Pi's 5 V pins (2 and 4) or the breadboard rails.
- Power and ground are joined on screw terminal blocks, not the breadboard (several amps at stall).
- A 470 µF capacitor across servo + and minus, stripe on minus, absorbs start-up spikes.

## Bare buzzer (2 legs) through a 2N3904

A bare buzzer can draw more than a GPIO pin should supply. Hold the 2N3904 flat side toward you,
legs down: left is E (emitter), middle is B (base), right is C (collector).

- Pi pin 11 (GPIO 17) → 1 kΩ resistor → B
- E → GND rail
- C → buzzer minus (the shorter leg, or the unmarked one)
- buzzer + → 5 V rail
- Optional for a magnetic buzzer: a 1N5817 diode across the buzzer, its stripe on the + side.

Set `buzzer.type` to `active` if it beeps by itself when powered, `passive` if it only clicks.

## LED eyes and the level shifter

The ring runs on 5 V and wants a 5 V data signal; the Pi gives 3.3 V. Through one channel of the
4-channel level shifter:

- LV → 3.3 V rail, HV → 5 V rail, both GND pins → GND rail
- Pi pin 19 (GPIO 10) → LV1, HV1 → ring DIN (the input arrow side, not DOUT)
- A 330 Ω resistor in series on the data line helps, if you have one.

Many rings also work with the data wire straight from pin 19; use the shifter if colors flicker or
are wrong. Set `eyes.num_pixels` to the number of LEDs. At `max_brightness: 0.3`, 12 to 24 LEDs
are fine on the Pi's 5 V.

## Pi config.txt lines

At the end of `/boot/firmware/config.txt`, then reboot:

```
dtoverlay=pwm-2chan,pin=12,func=4,pin2=13,func2=4
dtparam=spi=on
```

The mic adds `dtparam=i2s=on` and its overlay when audio is set up.

## Power

| Supply                        | Feeds                          | Notes                                          |
| ----------------------------- | ------------------------------ | ---------------------------------------------- |
| Pi 5 27 W USB-C supply        | Pi, webcam, mic, LEDs, buzzer  | Check `vcgencmd get_throttled` reads `0x0`     |
| 6 V 5000 mAh battery          | Both servos                    | Measure before use: keep under 7.4 V           |
| Fallback: 5 V 2.1 A supply    | Both servos, for bench testing | Fine for light moves; can sag if a servo stalls|

Connect the servo battery last and disconnect it first. Charge it only with a hobby charger in the
mode that matches its label (NiMH or LiFe), at about 2 A.

## Network

The Pi is `carebot.local`. Bench setup: Ethernet straight to a laptop; SSH works over the IPv6
link-local address. For the demo it moves to the team's travel router.
