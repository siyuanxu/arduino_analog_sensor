# Arduino Mega 2560 official Windows driver package

These two driver files are unmodified copies from the official
`Arduino AVR Boards` 1.8.8 Boards Manager archive:

- Source archive: `https://downloads.arduino.cc/cores/staging/avr-1.8.8.tar.bz2`
- Source archive SHA-256:
  `a234c2a43dcd01ce54be665806f183b8e6ec4e966d2e3e0c3358b63023d6390c`
- Package index: `https://downloads.arduino.cc/packages/package_index.json`
- Upstream source: `https://github.com/arduino/ArduinoCore-avr/tree/master/drivers`

The package is limited at runtime to an attached original Mega 2560 with
Windows problem code 28 and one of these hardware IDs:

- `USB\VID_2341&PID_0042` or `USB\VID_2341&PID_0010` uses `arduino.inf`.

The upstream INF file also lists other official Arduino boards because the
catalog signature covers the complete, unmodified driver package. The
application must never invoke installation for those other IDs. It does not
support or install CH340/CH341, FTDI, or other clone-board drivers.

The copyright and GPL-2.0-or-later redistribution terms with Arduino's special
exception are embedded at the start of `arduino.inf`. A complete, unmodified
copy of GPL 2.0 from
`https://www.gnu.org/licenses/old-licenses/gpl-2.0.txt` is provided as
`LICENSE-GPL-2.0.txt` (SHA-256
`edaef632cbb643e4e7a221717a6c441a4c1a7c918e6e4d56debc3d8739b233f6`).
The Arduino name is used only to identify compatible hardware. No Arduino
logo or endorsement is claimed.

The catalog file is kept byte-for-byte unchanged so Windows can validate its
original publisher signature. `SHA256SUMS.txt` pins both files used by this
application.

The same upstream archive also contains an `arduino-org.inf` package for
`VID_2A03`. That INF does not contain the GPL redistribution notice found in
`arduino.inf`, so it is intentionally not redistributed here. On current
Windows systems, supported `VID_2A03` Mega devices are repaired only with the
inbox Microsoft USB serial driver already present in Windows.
