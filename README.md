# PiSMS

Send SMS messages from a Raspberry Pi equiped with a GSM modem.

## Install

PiSMS requires:

  * Python 3.7+
  * python-messaging
  * PySerial
  * RPi.GPIO

PiSMS can be run directly from the project source using the `run.py` script.

    $ ./run.py

It can also be installed using Python Build/Install:

    $ python -m build --wheel
    $ sudo python -m installer dist/*.whl
