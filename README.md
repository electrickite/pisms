# PiSMS

Send SMS messages from a Raspberry Pi equipped with a GSM modem.

## Install

PiSMS requires:

  * Python 3.7+
  * python-messaging
  * PySerial
  * RPi.GPIO

Packaging and installation requires:

  * python-build
  * python-wheel

To install these dependencies in RaspberryPi OS:

    $ sudo apt-get install python3-pip
    $ sudo pip3 install build wheel

PiSMS can be run directly from the project source using the `run.py` script.

    $ ./run.py

It can also be installed using Python build and pip:

    $ python -m build --wheel
    $ sudo pip3 install dist/*.whl

## Use

PiSMS will communicate over a serial port with the GSM modem using AT commands.
A few common commands:

    $ pisms --help             # Show usage information
    $ pisms info               # Run several modem information commands
    $ pisms -p /dev/AMA0 info  # Use the serial device at /dev/AMA0
    $ pisms recv               # Check for receieved messages (JSON format)
    $ pisms recv --help        # Get help for recv command
    $ pisms send 5555555555    # Send an SMS message from stdin to 5555555555
    $ pisms at ATI             # Send raw ATI command to modem

### Commands and options

```
PiSMS has the following general options and commands:

usage: pisms [-h] [-p PORT] [-b BAUD] [-m MODE] [-g PWRPIN]
  [-u PWRUP] [-d PWRDOWN] [-w WAIT] [-l] [--log LOG] [-v] command ...

Send and receive SMS messages.

optional arguments:
  -h, --help            show this help message and exit
  -p PORT, --port PORT  Serial device path
  -b BAUD, --baud BAUD  Serial baud rate
  -m MODE, --mode MODE  Serial mode (default: 8N1)
  -g PWRPIN, --pwrpin PWRPIN
                        Modem GPIO power pin
  -u PWRUP, --pwrup PWRUP
                        Power up GPIO pulse (seconds)
  -d PWRDOWN, --pwrdown PWRDOWN
                        Power down GPIO pulse (seconds)
  -w WAIT, --wait WAIT  Time to wait for network connection (seconds)
  -l, --pwrlow          GPIO power pin active low
  --log LOG             Set log level
  -v, --version         show program's version number and exit

Commands:
  Use -h to see arguments and options for each command

  command
    send                Send SMS message. The message text is read from stdin.
    recv                Check for received SMS messages.
    monitor             Listen for new SMS notifications.
    clear               Clear all messages from SIM.
    info                Query modem information.
    at                  Send AT command.
```

### Modem power control

PiSMS can optionally send pulses on a GPIO pin to power the modem on or off.
Depending on the various `pwr` options, a continuous high/low level can be set
or a short pulse can be sent on the configured pin. 
