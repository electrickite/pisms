"""
Copyright (c) 2023 Corey Hinshaw

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

import argparse
import json
import logging as log
import re
import serial
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta
from messaging.sms import SmsSubmit
from messaging.sms import SmsDeliver


_nonbmp = re.compile(r'[\U00010000-\U0010FFFF]')

def _surrogatepair(match):
    char = match.group()
    assert ord(char) > 0xffff
    encoded = char.encode('utf-16-le')
    return (
        chr(int.from_bytes(encoded[:2], 'little')) +
        chr(int.from_bytes(encoded[2:], 'little')))

def _handle_exit(sig, frame):
    raise(SystemExit)


class App:
    def __init__(self):
        self.cmd = None
        self.number = None
        self.ser = None
        self.powered = False

    def power_up(self):
        if not self.pwrpin:
            log.debug("Power pin not set, skipping modem power up")
            return

        if self.pwrup:
            log.info("Sending modem power up signal on GPIO pin %d", self.pwrpin)
            GPIO.output(self.pwrpin, self.active)
            time.sleep(self.pwrup)
            GPIO.output(self.pwrpin, self.inactive)
        else:
            log.info("Setting modem GPIO pin %d active", self.pwrpin)
            GPIO.output(self.pwrpin, self.active)
        self.powered = True
        self.ser.reset_input_buffer()
        self.ser.reset_output_buffer()

    def power_down(self):
        if not self.powered:
            log.debug("No modem power control, skipping power down")
            return

        if self.pwrdown:
            log.info("Sending modem power down signal on GPIO pin %d", self.pwrpin)
            GPIO.output(self.pwrpin, self.active)
            time.sleep(self.pwrdown)
            GPIO.output(self.pwrpin, self.inactive)
            time.sleep(2)
        else:
            log.info("Setting modem GPIO pin %d inactive", self.pwrpin)
            GPIO.output(self.pwrpin, self.inactive)

    def parse_args(self):
        parser = argparse.ArgumentParser(
            prog="pisms",
            description="Send and receive SMS messages.",
            fromfile_prefix_chars='@'
        )
        subparsers = parser.add_subparsers(
            title='Commands',
            description='Use -h to see arguments and options for each command',
            required=True,
            metavar="command",
        )

        parser.add_argument("-p", "--port", default="/dev/ttyS0", help="Serial device path")
        parser.add_argument("-b", "--baud", type=int, default=115200, help="Serial baud rate")
        parser.add_argument("-m", "--mode", default="8N1", help="Serial mode (default: 8N1)")
        parser.add_argument("-g", "--pwrpin", type=int, default=0, help="Modem GPIO power pin")
        parser.add_argument("-u", "--pwrup", type=int, default=0, help="Power up GPIO pulse (seconds)")
        parser.add_argument("-d", "--pwrdown", type=int, default=0, help="Power down GPIO pulse (seconds)")
        parser.add_argument("-w", "--wait", type=int, default=18, help="Time to wait for network connection (seconds)")
        parser.add_argument("-l", "--pwrlow", action='store_true', help="GPIO power pin active low")
        parser.add_argument("--log", default="WARNING", help="Set log level")
        parser.add_argument("-v", "--version", action="version", version="%(prog)s 0.1.0")

        send = subparsers.add_parser('send', help='Send SMS message. The message text is read from stdin.')
        send.add_argument("number", help="Recipient phone number")
        send.set_defaults(cmd=self.send)

        recv = subparsers.add_parser('recv', help='Check for received SMS messages.')
        recv.add_argument("-a", "--maxage", type=int, default=1, help="Maximum age of stored message fragments (hours)")
        del_opts = recv.add_mutually_exclusive_group()
        del_opts.add_argument("-p", "--preserve", action='store_true', help="Do not delete listed messages from SIM memory")
        del_opts.add_argument("-D", "--deleteall", action='store_true', help="Delete all received messages from SIM memory")
        recv.set_defaults(cmd=self.receive)

        mon = subparsers.add_parser('monitor', help='Listen for new SMS notifications.')
        mon.add_argument("-c", "--command", help="Run shell command for each notification")
        mon.set_defaults(cmd=self.monitor)

        clr = subparsers.add_parser('clear', help='Clear all messages from SIM.')
        clr.set_defaults(cmd=self.clear)

        info = subparsers.add_parser('info', help='Query modem information.')
        info.set_defaults(cmd=self.modem_info)

        at = subparsers.add_parser('at', help='Send AT command.')
        at.add_argument("command", help="Raw AT command")
        at.add_argument("back", nargs='?', default="OK", help="Expected success response (default: OK)")
        at.add_argument("timeout", type=int, nargs='?', default=2, help="Maximum time to wait for response (seconds)")
        at.set_defaults(cmd=self.at)

        parser.parse_args(namespace=self)
        log.basicConfig(level=getattr(log, self.log.upper()))
        self.bytesize = int(self.mode[0])
        self.parity = self.mode[1]
        self.stopbits = int(self.mode[2])
        if self.pwrpin:
            self.active = GPIO.LOW if self.pwrlow else GPIO.HIGH
            self.inactive = GPIO.HIGH if self.pwrlow else GPIO.LOW

    def read_message(self):
        if sys.stdin.isatty():
            print("Enter message, press Ctrl-D on blank line to send")
            print('> ', end='', flush="True")
        message = sys.stdin.read().strip()
        if not message:
            raise ValueError("No message on stdin!")
        return message

    def setup(self):
        log.info("Setting up serial port %s at %d %s", self.port, self.baud, self.mode)
        self.ser = serial.Serial(
            port=self.port,
            baudrate=self.baud,
            bytesize=self.bytesize,
            parity=self.parity,
            stopbits=self.stopbits,
            write_timeout=2,
        )
        self.ser.reset_input_buffer()
        self.ser.reset_output_buffer()

        if self.pwrpin:
            import RPi.GPIO as GPIO
            log.debug("Setup GPIO pin %d", self.pwrpin)
            GPIO.setmode(GPIO.BCM)
            GPIO.setwarnings(False)
            GPIO.setup(self.pwrpin, GPIO.OUT)
            time.sleep(0.1)
            GPIO.output(self.pwrpin, self.inactive)

    def at_command(self, command, back, timeout, quiet=False):
        log.debug("AT command: %s", command)
        self.ser.reset_input_buffer()
        self.ser.write(b'\x1B')
        self.ser.write((command+'\r\n').encode())
        self.ser.timeout = timeout
        rec_buff = ''
        rec_buff = self.ser.read_until(expected=back.encode())
        response = rec_buff.decode()
        if back not in response:
            if not quiet:
                log.error('AT ERROR:\t%s', command)
                log.error('AT Response:\t%s', response)
            return False, response
        else:
            log.debug(response)
            return True, response

    def with_surrogates(self, text):
        return _nonbmp.sub(_surrogatepair, text)

    def send(self):
        message = self.read_message()
        sms = SmsSubmit(self.number, self.with_surrogates(message))
        log.info("Sending SMS message to %s", self.number)
        print("Sending SMS message to", self.number, file=sys.stderr)

        self.power_up()
        self.check_connection()
        self.at_command("AT+CMGF=0", "OK", 1)

        for pdu in sms.to_pdu():
            result, _ = self.at_command('AT+CMGS=%d\r' % pdu.length, ">", 2)
            if result:
                log.debug(pdu.pdu)
                self.ser.write(pdu.pdu.encode())
                self.ser.write(b'\x1A')
                result, _ = self.at_command('', 'OK', 5)
                if not result:
                    log.error("Error sending message!")
                    print("Error sending message!", file=sys.stderr)
                    return False
        log.info("Message sent successfully")
        print("Message sent successfully", file=sys.stderr)
        return True

    def list_messages(self, status=0):
        messages = []
        result, response = self.at_command('AT+CMGL=%d' % status, 'OK', 2)
        if result:
            matches = re.findall('\+CMGL:\s([0-9]+),.*[\r\n]+([^\r\n]+)', response)
            if matches:
                for message in matches:
                    sms = SmsDeliver(message[1])
                    data = sms.data
                    data['idx'] = message[0]
                    data['text'] = data['text'].encode('utf-16','surrogatepass').decode('utf-16')
                    messages.append(data)
        return result, messages

    def collect_fragments(self, fragments, data=None, process=False):
        if data:
            if data['ref'] in fragments:
                fragments[data['ref']].append(data)
            else:
                fragments[data['ref']] = [data]
        if not process:
            return

        messages = []
        read_idx = []
        for ref in fragments.keys():
            if len(fragments[ref]) == fragments[ref][0]['cnt']:
                fragments[ref].sort(key=lambda f:f['seq'])
                text = ''
                for fragment in fragments[ref]:
                    text += fragment['text']
                    read_idx.append(fragment['idx'])
                message = fragments[ref][-1]
                del message['ref']
                del message['cnt']
                del message['seq']
                message['text'] = text
                messages.append(message)
        return messages, read_idx

    def receive(self):
        log.info("Checking for received messages")
        print("Checking for received messages", file=sys.stderr)
        self.power_up()
        self.at_command("AT+CMGF=0", "OK", 1)
        self.at_command('AT+CPMS="SM","SM","SM"', 'OK', 1)
        read_idx = []
        fragments = {}
        messages = []

        result, sim_read = self.list_messages(status=1)
        if result:
            now = datetime.now()
            for message in sim_read:
                if message['date'] < now-timedelta(hours=self.maxage):
                    read_idx.append(message['idx'])
                if 'ref' in message:
                    self.collect_fragments(fragments, message)

        result, unread = self.list_messages(status=0)
        if result:
            for message in unread:
                if 'ref' in message:
                    self.collect_fragments(fragments, message)
                else:
                    messages.append(message)
                    read_idx.append(message['idx'])
            long_messages, frag_read_idx = self.collect_fragments(fragments, process=True)
            print(json.dumps(messages + long_messages, indent=2, default=str, ensure_ascii=False))
            if self.deleteall:
                self.at_command("AT+CMGD=1,1", "OK", 2)
            elif not self.preserve:
                for idx in read_idx + frag_read_idx:
                    self.at_command("AT+CMGD=%s" % idx, "OK", 2)
            return True
        log.error("Error checking messages!")
        print("Error checking messages!", file=sys.stderr)
        return False

    def clear(self):
        log.info("Clearing all SMS messages from modem storage")
        print("Clearing all SMS messages from modem storage", file=sys.stderr)
        self.power_up()
        result, _ = self.at_command("AT+CMGD=1,4", "OK", 2)
        return result

    def check_connection(self):
        log.debug("Waiting %d seconds for mobile network connection", self.wait)
        t_end = time.time() + self.wait
        while time.time() < t_end:
            result, response = self.at_command("AT+CREG?", "OK", 1, quiet=True)
            if result:
                match = re.search(r"\+CREG:\s[0-9],(?P<status>[0-9])", response)
                if match and match.group('status') in ('1','5','6','7'):
                    log.info("Network connection established")
                    return
            time.sleep(1)
        raise RuntimeError("No mobile network connection")

    def modem_info(self):
        print("Querying modem information...")
        self.power_up()

        _, res = self.at_command("ATI", "OK", 1)
        print(res)
        _, res = self.at_command("AT+CPIN?", "OK", 1)
        print(res)
        _, res = self.at_command("AT+CIMI", "OK", 1)
        print("AT+CIMI")
        print(res)
        _, res = self.at_command("AT+CSQ", "OK", 1)
        print(res)
        _, res = self.at_command("AT+CREG?", "OK", 1)
        print(res)
        _, res = self.at_command("AT+COPS?", "OK", 1)
        print(res)

    def at(self):
        log.info("Sending specified AT command")
        print(self.command)
        _, res = self.at_command(self.command, self.back, self.timeout)
        print(res)

    def monitor(self):
        log.info("Listening for +CMTI messages on %s" % self.port)
        print("Listening for +CMTI messages on %s" % self.port)
        while True:
            line = self.ser.readline().decode()
            log.debug("Read line: %s", line.rstrip())
            if "+CMTI:" in line:
                log.info("+CMTI received")
                print("%s New message received" % datetime.now())
                if self.command:
                    log.info("Running command: %s", self.command)
                    p = subprocess.run(self.command, shell=True, capture_output=True)
                    log.debug("Return code: %d", p.returncode)
                    log.debug("stdout: %s", p.stdout.decode())
                    log.debug("stderr: %s", p.stderr.decode())
        return True

    def cleanup(self):
        log.info("Closing serial port and cleaning up GPIO")
        if self.ser != None:
            self.ser.close()
        self.power_down()
        if 'GPIO' in sys.modules:
            GPIO.cleanup()

    def run(self):
        status = 0
        self.parse_args()
        self.setup()

        try:
            status = 0 if self.cmd() else 1
        except (KeyboardInterrupt, SystemExit):
            log.info("Received interrupt. Exiting...")
        finally:
            self.cleanup()
        return status


def run():
    signal.signal(signal.SIGTERM, _handle_exit)
    app = App()
    return app.run()
