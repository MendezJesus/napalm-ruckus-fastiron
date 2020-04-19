# Copyright 2015 Spotify AB. All rights reserved.
#
# The contents of this file are licensed under the Apache License, Version 2.0
# (the "License"); you may not use this file except in compliance with the
# License. You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations under
# the License.

# Python3 support
from __future__ import print_function
from __future__ import unicode_literals

# std libs
# import sys
from netmiko import ConnectHandler
import socket
import sys
# import re

# local modules
# import napalm.base.exceptions
# import napalm.base.helpers
from napalm.base.exceptions import ReplaceConfigException, \
    MergeConfigException, ConnectionException, ConnectionClosedException

# import napalm.base.constants as c
# from napalm.base import validate
from napalm.base import NetworkDriver


class FastIronDriver(NetworkDriver):
    """Napalm driver for FastIron."""

    def __init__(self, hostname, username, password, timeout=60, optional_args=None):
        """Constructor."""

        if optional_args is None:
            optional_args = {}

        self.device = None
        self.hostname = hostname
        self.username = username
        self.password = password
        self.timeout = timeout
        self.port = optional_args.get('port', 22)
        self.merge_config = False
        self.replace_config = False
        self.stored_config = None
        self.config_replace = None
        self.config_merge = None
        self.rollback_cfg = optional_args.get('rollback_cfg', 'rollback_config.txt')
        self.image_type = None

    def __del__(self):
        """
        This method is used to cleanup when the program is terminated suddenly.
        We need to make sure the connection is closed properly and the configuration DB
        is released (unlocked).
        """
        self.close()

    def open(self):
        """
        Opens a connection to the device.
        """
        try:
            self.device = ConnectHandler(device_type='ruckus_fastiron',
                                         ip=self.hostname,      # saves device parameters
                                         port=self.port,
                                         username=self.username,
                                         password=self.password,
                                         timeout=self.timeout,
                                         verbose=True)
            self.device.session_preparation()
            # image_type = self.device.send_command("show version")   # find the image type
            # if image_type.find("SPS") != -1:
            #     self.image_type = "Switch"
            # else:
            #     self.image_type = "Router"

        except Exception:
            raise ConnectionException("Cannot connect to switch: %s:%s" % (self.hostname,
                                                                           self.port))

    def close(self):
        """
        Closes the connection to the device.
        """
        self.device.disconnect()

    def is_alive(self):
        """
        Returns a flag with the connection state.
        Depends on the nature of API used by each driver.
        The state does not reflect only on the connection status (when SSH), it must also take into
        consideration other parameters, e.g.: NETCONF session might not be usable, although the
        underlying SSH session is still open etc.
        """
        null = chr(0)
        try:                                # send null byte see if alive
            self.device.send_command(null)
            return {'is_alive': self.device.remote_conn.transport.is_active()}

        except (socket.error, EOFError):
            return {'is_alive': False}
        except AttributeError:
            return {'is_alive': False}

    def _send_command(self, command):
        """Wrapper for self.device.send.command().

        If command is a list will iterate through commands until valid command.
        """
        output = ""

        try:
            if isinstance(command, list):
                for cmd in command:
                    output = self.device.send_command(cmd)
                    if "% Invalid" not in output:
                        break
            else:
                output = self.device.send_command(command)
            return output
        except (socket.error, EOFError) as e:
            raise ConnectionClosedException(str(e))

    class PortSpeedException(Exception):
        """Raised when port speed does not match available inputs"""

        def __init_(self, arg):
            print("unexpected speed: %s please submit bug with port speed" % arg)
            sys.exit(1)

    @staticmethod
    def __retrieve_all_locations(long_string, word, pos):
        """Finds a word of a long_string and returns the value in the nth position"""
        count = 0                           # counter
        split_string = long_string.split()  # breaks long string into string of substring
        values = []                         # creates a list
        for m in split_string:              # goes through substrings one by one
            count += 1                      # increments counter
            if m == word:                   # if substring and word match then specific value
                values.append(split_string[count + pos])    # is added to list that is returned
        return values

    @staticmethod
    def __find_words(output, word_list, pos_list):
        """   """
        dictionary = {}
        if len(word_list) != len(pos_list):             # checks word, pos pair exist
            return None

        if len(word_list) == 0 or len(pos_list) == 0:   # returns NONE if list is empty
            return None

        size = len(word_list)
        sentence = output.split()                   # breaks long string into separate strings

        for m in range(0, size):                    # Iterates through size of word list
            pos = int(pos_list.pop())               # pops element position and word pair in list
            word = word_list.pop()
            if word in sentence:                    # checks if word is contained in text
                indx = sentence.index(word)         # records the index of word
                dictionary[word] = sentence[indx + pos]

        return dictionary

    @staticmethod
    def __creates_list_of_nlines(my_string):
        """ Breaks a long string into separated substring"""
        temp = ""                       # sets empty string, will add char respectively
        my_list = list()                # creates list
        for val in range(0, len(my_string)):    # iterates through the length of input

            if my_string[val] == '\n' and temp == "":
                continue
            elif my_string[val] == '\n' or val == len(my_string) - 1:    # add what was found
                my_list.append(temp)
                temp = ""
            else:
                temp += my_string[val]

        return my_list

    @staticmethod
    def __delete_if_contains(nline_list, del_word):
        temp_list = list()                          # Creates a list to store variables
        for a_string in nline_list:                 # iterates through list
            if del_word in a_string:                # if word matches, word is skipped
                continue
            else:
                temp_list.append(a_string.split())  # Word didn't match store in list
        return temp_list

    @staticmethod
    def __facts_uptime(my_string):  # TODO check for hours its missing....
        my_list = ["day(s)", "hour(s)", "minute(s)", "second(s)"]   # list of words to find
        my_pos = [-1, -1, -1, -1]                   # relative position of interest
        total_seconds = 0                           # data variables
        multiplier = 0
        t_dictionary = FastIronDriver.__find_words(my_string, my_list, my_pos)    # retrieves pos

        for m in t_dictionary.keys():               # Checks word found and its multiplier
            if m == "second(s)":                    # converts to seconds
                multiplier = 1
            elif m == "minute(s)":
                multiplier = 60
            elif m == "hour(s)":
                multiplier = 3600
            elif m == "day(s)":
                multiplier = 86400
            total_seconds = int(t_dictionary.get(m))*multiplier + total_seconds
        return total_seconds

    @staticmethod
    def __facts_model(string):
        model = FastIronDriver.__retrieve_all_locations(string, "Stackable", 0)[0]
        return model                                # returns the model of the switch

    @staticmethod
    def __facts_hostname(string):
        if "hostname" in string:
            hostname = FastIronDriver.__retrieve_all_locations(string, "hostname", 0)[0]
            return hostname                         # returns the hostname if configured
        else:
            return None

    @staticmethod
    def __facts_os_version(string):
        os_version = FastIronDriver.__retrieve_all_locations(string, "SW:", 1)[0]
        return os_version                           # returns the os_version of switch

    @staticmethod
    def __facts_serial(string):
        serial = FastIronDriver.__retrieve_all_locations(string, "Serial", 0)[0]
        serial = serial.replace('#:', '')
        return serial                               # returns serial number

    @staticmethod
    def __physical_interface_list(shw_int_brief, only_physical=True):
        interface_list = list()
        n_line_output = FastIronDriver.__creates_list_of_nlines(shw_int_brief)

        for line in n_line_output:
            line_list = line.split()
            if only_physical == 1:
                interface_list.append(line_list[0])
        return interface_list

    @staticmethod
    def __facts_interface_list(shw_int_brief, pos=0, del_word="Port", trigger=0):
        interfaces_list = list()
        n_line_output = FastIronDriver.__creates_list_of_nlines(shw_int_brief)

        interface_details = FastIronDriver.__delete_if_contains(n_line_output, del_word)

        for port_det in interface_details:

            if trigger == 0:
                interfaces_list.append(port_det[pos])
            else:                                           # removes non physical interface
                if any(x in port_det[pos] for x in ["ve", "lb", "tunnel"]):
                    continue
                else:
                    interfaces_list.append(port_det[pos])       # adds phys interface to list
        return interfaces_list

    @staticmethod
    def __port_time(shw_int_port):
        t_port = list()                                         # Creates n lines of show int port
        new_lines = FastIronDriver.__creates_list_of_nlines(shw_int_port)

        for val in new_lines:
            if "name" in val:
                continue
            t_port.append(FastIronDriver.__facts_uptime(val))     # adds time to ports

        return t_port

    @staticmethod
    def __get_interface_speed(shw_int_speed):
        speed = list()                                          # creates list
        for val in shw_int_speed:                               # speed words contained and compared
            if val == 'auto,' or val == '1Gbit,':               # appends speed hat
                speed.append(1000)
            elif val == '10Mbit,':
                speed.append(10)
            elif val == '100Mbit,':
                speed.append(100)
            elif val == '2.5Gbit,':
                speed.append(2500)
            elif val == '5Gbit,':
                speed.append(5000)
            elif val == '10Gbit,':
                speed.append(10000)
            elif val == '40Gbit,':
                speed.append(40000)
            elif val == '100Gbit,':
                speed.append(100000)
            else:
                raise FastIronDriver.PortSpeedException(val)

        return speed

    @staticmethod
    def __unite_strings(output):
        """ removes all the new line and excess spacing in a string"""
        my_string = ""                              # empty string

        for index in range(len(output)):            # iterates through all characters of output

            if output[index] != '\n' and output[index] != ' ':  # skips newline and spaces
                my_string += output[index]

            if index != len(output) - 1:
                if output[index] == ' ' and output[index+1] != ' ':
                    my_string += ' '                # next char of string is not another space

        return my_string                            # returns stored string

    @staticmethod
    def __get_interface_name(shw_int_name, size):
        port_status = list()                            # Creates list
        shw_int_name = FastIronDriver.__creates_list_of_nlines(shw_int_name)
        for val in shw_int_name:                        # iterates through n lines
            if "No port name" in val:
                port_status.append("")                  # appends nothing for port name
            else:
                port_status.append(val.replace("Port name is", ""))     # Removes fluff add name

        for temp in range(0, size - len(port_status)):  # adds no names to the remainder so that
            port_status.append("")                      # all matrix of data are the same size

        return port_status

    @staticmethod
    def __is_greater(value, threshold):               # compares two values returns true if value
        if float(value) >= float(threshold):        # is greater or equal to threshold
            return True
        return False

    @staticmethod
    def __get_interfaces_speed(shw_int_speed, size):
        port_status = list()                            # Create a list
        for val in range(0, size):
            if val < len(shw_int_speed):
                port_status.append(shw_int_speed[val])  # appends string index into port list
            else:
                port_status.append(0)
        return port_status                              # returns port list

    @staticmethod
    def __matrix_format(my_input):
        my_list = list()
        newline = FastIronDriver.__creates_list_of_nlines(my_input)
        for text in newline:                            # Goes through n lines by n lines
            text = text.split()                         # splits long string into words
            if len(text) < 1:                           # if more than a single word skip
                continue
            else:
                my_list.append(text)                    # appends single word

        return my_list                                  # returns list

    @staticmethod
    def __environment_temperature(string):
        dic = dict()
        temp = FastIronDriver.__retrieve_all_locations(string, "(Sensor", -3)
        warning = FastIronDriver.__retrieve_all_locations(string, "Warning", 1)
        shutdown = FastIronDriver.__retrieve_all_locations(string, "Shutdown", 1)
        for val in range(0, len(temp)):
            crit = FastIronDriver.__is_greater(temp[val], shutdown[0])
            alert = FastIronDriver.__is_greater(temp[val], warning[0])
            dic.update({'sensor ' + str(val + 1): {'temperature': float(temp[val]),
                                                   'is_alert': alert,
                                                   'is_critical': crit}})

        return {'temperature': dic}                     # returns temperature of type dictionary

    @staticmethod
    def __environment_cpu(string):
        cpu = max(FastIronDriver.__retrieve_all_locations(string, "percent", -2))
        dic = {'%usage': cpu}
        return {'cpu': dic}                             # returns dictionary with key cpu

    @staticmethod
    def __environment_power(chassis_string, inline_string):
        status = FastIronDriver.__retrieve_all_locations(chassis_string, "Power", 4)
        potential_values = FastIronDriver.__retrieve_all_locations(chassis_string, "Power", 1)
        norm_stat = FastIronDriver.__retrieve_all_locations(chassis_string, "Power", 7)
        capacity = float(FastIronDriver.__retrieve_all_locations(inline_string,
                                                                 "Free", -4)[0]) / 1000
        pwr_used = capacity - float(FastIronDriver.__retrieve_all_locations(inline_string,
                                                                            "Free", 1)[0]) / 1000

        my_dic = {}  # creates new list
        for val in range(0, len(status)):               # if power supply has failed will return
            if status[val] == 'failed':                 # false, if working will return true
                my_dic["PSU" + potential_values[val]] = {'status': False,
                                                         'capacity': 0.0,
                                                         'output': 0.0}
            elif norm_stat[val] == "ok":
                my_dic["PS" + potential_values[val]] = {'status': True,
                                                        'capacity': capacity,
                                                        'output': pwr_used}

        return {'power': my_dic}                        # returns dictionary containing pwr info

    @staticmethod
    def __environment_fan(string):
        fan = FastIronDriver.__retrieve_all_locations(string, "Fan", 1)
        unit = FastIronDriver.__retrieve_all_locations(string, "Fan", 0)
        my_dict = {}  # creates list

        if "Fanless" in string:
            return {"fan": {None}}                      # no fans are in unit and returns None

        for val in range(0, len(fan)):
            if fan[val] == "ok,":                       # checks if output is failed or ok
                my_dict["fan" + unit[val]] = {'status': True}
            elif fan[val] == "failed":                  # if fan fails, will return false
                my_dict["fan" + unit[val]] = {'status': False}

        return {'fan': my_dict}                         # returns dictionary containing fan info

    @staticmethod
    def __environment_memory(string):
        mem_total = FastIronDriver.__retrieve_all_locations(string, "Dynamic", 1)
        mem_used = FastIronDriver.__retrieve_all_locations(string, "Dynamic", 4)
        dic = {'available_ram': int(mem_total[0]), 'used_ram': int(mem_used[0])}

        return {'memory': dic}

    @staticmethod
    def __output_parser(output, word):
        """If the word is found in the output, it will return the ip
            address until a new interface is found."""
        token = output.find(word) + len(word)           # saves pos of where word is contained
        count = 0                                       # counter variable
        output = output[token:len(output)].replace('/', ' ')
        nline = FastIronDriver.__creates_list_of_nlines(output)
        ip6_dict = dict()                               # creates dictionary

        for sentence in nline:                          # separated n lines goes n line by n line
            sentence = sentence.split()                 # sentence contains list of words

            if len(sentence) > 2:                       # if length of list is greater than 2
                count += 1                              # its a parent interface
                if count > 1:                           # only a single parent interface at a time
                    break                               # breaks if another parent interface found
                ip6_dict.update({                       # Update ipv6 dict with ipv6 add and mask
                        sentence[2]: {'prefix_length': sentence[3]}
                })
            if len(sentence) == 2:                      # child ipv6 interface is found
                ip6_dict.update({                       # updates dictionary with ipv6 and mask
                        sentence[0]: {'prefix_length': sentence[1]}
                })

        return ip6_dict                                 # returns ipv6 dictionary

    @staticmethod
    def __creates_config_block(list_1):
        config_block = list()
        temp_block = list()

        for line_cmd in list_1:
            cmd_position = list_1.index(line_cmd)
            if cmd_position != 0:
                if list_1[cmd_position - 1] == '!':
                    while list_1[cmd_position] != '!' and cmd_position < len(list_1) - 1:
                        temp_block.append(list_1[cmd_position])
                        cmd_position += 1

                    if len(temp_block) > 0:
                        config_block.append(temp_block)
                    temp_block = list()

        return config_block

    @staticmethod
    def __compare_blocks(cb_1, config_blocks_2, cmd, symbol):
        temp_list = list()
        for cb_2 in config_blocks_2:                # grabs a single config block
            if cmd == cb_2[0]:                      # checks cmd not found
                stat = True
                for single_cmd in cb_1:             # iterates through cmd of config block
                    if single_cmd == cmd:           # if this is first command add as base
                        temp_list.append(single_cmd)  # add to list with no changes
                    elif single_cmd not in cb_2:
                        temp_list.append(symbol + " " + single_cmd)
        return temp_list, stat

    @staticmethod
    def __comparing_list(list_1, list_2, symbol):
        diff_list = list()
        config_blocks_1 = FastIronDriver.__creates_config_block(list_1)
        config_blocks_2 = FastIronDriver.__creates_config_block(list_2)

        for cb_1 in config_blocks_1:                # Grabs a single config block
            is_found = False

            if cb_1 not in config_blocks_2:         # checks if config block already exisit
                cmd = cb_1[0]                       # grabs first cmd of config block

                temp_list, is_found = FastIronDriver.__compare_blocks(cb_1, config_blocks_2,
                                                                      cmd, symbol)

                if is_found == 0:
                    for value in cb_1:
                        temp_list.append(symbol + " " + value)

            if len(temp_list) > 1:
                diff_list.append(temp_list)

        return diff_list

    @staticmethod
    def __compare_away(diff_1, diff_2):
        mystring = ""

        for cb_1 in diff_1:
            mystring += cb_1[0] + '\n'
            for cb_2 in diff_2:
                if cb_1[0] in cb_2:
                    for value_2 in range(1, len(cb_2)):
                        mystring += cb_2[value_2] + '\n'
            for input_1 in range(1, len(cb_1)):
                mystring += cb_1[input_1] + '\n'

        return mystring

    @staticmethod
    def __compare_vice(diff_2, diff_1):
        mystring = ""

        for cb_2 in diff_2:
            found = False
            for cb_1 in diff_1:
                if cb_2[0] in cb_1:
                    found = True

            if found == 0:
                for input_2 in cb_2:
                    mystring += input_2 + '\n'

        return mystring

    def load_replace_candidate(self, filename=None, config=None):
        """
        Populates the candidate configuration. You can populate it from a file or from a string.
        If you send both a filename and a string containing the configuration, the file takes
        precedence.

        If you use this method the existing configuration will be replaced entirely by the
        candidate configuration once you commit the changes. This method will not change the
        configuration by itself.

        :param filename: Path to the file containing the desired configuration. By default is None.
        :param config: String containing the desired configuration.
        :raise ReplaceConfigException: If there is an error on the configuration sent.
        """
        file_content = ""

        if filename is None and config is None:             # if nothing is entered returns none
            print("No filename or config was entered")
            return None

        if filename is not None:
            try:
                file_content = open(filename, "r")          # attempts to open file
                temp = file_content.read()                  # stores file content
                self.config_replace = FastIronDriver.__creates_list_of_nlines(temp)
                self.replace_config = True                  # file opened successfully
                return
            except ValueError:
                raise ReplaceConfigException("Configuration error")

        if config is not None:
            try:
                self.config_replace = FastIronDriver.__creates_list_of_nlines(config)
                self.replace_config = True                  # string successfully saved
                return
            except ValueError:
                raise ReplaceConfigException("Configuration error")

        raise ReplaceConfigException("Configuration error")

    def load_merge_candidate(self, filename=None, config=None):
        """
        Populates the candidate configuration. You can populate it from a file or from a string.
        If you send both a filename and a string containing the configuration, the file takes
        precedence.

        If you use this method the existing configuration will be merged with the candidate
        configuration once you commit the changes. This method will not change the configuration
        by itself.

        :param filename: Path to the file containing the desired configuration. By default is None.
        :param config: String containing the desired configuration.
        :raise MergeConfigException: If there is an error on the configuration sent.
        """
        file_content = ""

        if filename is None and config is None:             # if nothing is entered returns none
            print("No filename or config was entered")
            return None

        if filename is not None:
            try:
                file_content = open(filename, "r")          # attempts to open file
                temp = file_content.read()                  # stores file content
                self.config_merge = FastIronDriver.__creates_list_of_nlines(temp)
                self.merge_config = True                    # file opened successfully
                return
            except ValueError:
                raise MergeConfigException("Configuration error")

        if config is not None:
            try:
                self.config_merge = FastIronDriver.__creates_list_of_nlines(config)
                self.merge_config = True                    # string successfully saved
                return
            except ValueError:
                raise MergeConfigException("Configuration error")

        raise MergeConfigException("Configuration error")

    def get_arp_table(self, vrf=""):
        """
        Returns a list of dictionaries having the following set of keys:
            * interface (string)
            * mac (string)
            * ip (string)
            * age (float)
        """
        output = self.device.send_command('show arp')
        token = output.find('Status') + len('Status') + 1
        vtoken = output.find('VLAN') + len('VLAN') + 1

        if vtoken != 0:                # router version, does not contain default vlan in arp
            token = vtoken             # defaults to switch version

        output = FastIronDriver.__creates_list_of_nlines(output[token:len(output)])
        arp_table = list()

        for val in output:
            check = val
            if len(check.split()) < 7:
                continue

            if vtoken == 0:
                __, ip, mac, __, age, interface, __ = val.split()
            else:
                __, ip, mac, __, age, interface, __, vlan = val.split()

            arp_table.append({
                'interface': interface,
                'mac': mac,
                'ip': ip,
                'age': float(age),
            })

        return arp_table
    
    def get_ntp_peers(self):

        """
        Returns the NTP peers configuration as dictionary.
        The keys of the dictionary represent the IP Addresses of the peers.
        Inner dictionaries do not have yet any available keys.
        Example::
            {
                '192.168.0.1': {},
                '17.72.148.53': {},
                '37.187.56.220': {},
                '162.158.20.18': {}
            }
        """
        output = self.device.send_command('show ntp associations')
        token = output.find('disp') + len('disp') + 1
        output = output[token:len(output)]
        nline = FastIronDriver.__creates_list_of_nlines(output)
        ntp_peers = dict()
        for val in range(len(nline)-1):
            val = nline[val].replace("~", " ")
            val = val.split()
            ntp_peers.update({
                val[1]: {}
            })

        return ntp_peers
