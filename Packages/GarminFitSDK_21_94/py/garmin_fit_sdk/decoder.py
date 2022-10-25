'''decoder.py: Contains the decoder class which is used to decode fit files.'''

###########################################################################################
# Copyright 2022 Garmin International, Inc.
# Licensed under the Flexible and Interoperable Data Transfer (FIT) Protocol License; you
# may not use this file except in compliance with the Flexible and Interoperable Data
# Transfer (FIT) Protocol License.
###########################################################################################
# ****WARNING****  This file is auto-generated!  Do NOT edit this file.
# Profile Version = 21.94Release
# Tag = production/akw/21.94.00-0-g0f668193
############################################################################################


import copy

from attr import field

from . import Accumulator, BitStream, CrcCalculator
from . import fit as FIT
from . import hr_mesg_utils, util
from .profile import Profile
from .stream import Endianness, Stream

_CRCSIZE = 2
_MESG_DEFINITION_MASK = 0x40
_MESG_HEADER_MASK = 0x00
_LOCAL_MESG_NUM_MASK = 0x0F
_DEV_DATA_MASK = 0x20


class Decoder:
    '''
    A class for decoding a given stream (fit file). Will return the decoded data
    from the stream

    Attributes:
        _stream: The given stream of data to be decoded.
        _local_mesg_defs: The 16 most recent message definitions read.
        _messages: The messages decoded by the Decoder.
    '''

    def __init__(self, stream: Stream):
        if stream is None:
            raise RuntimeError("FIT Runtine Error stream parameter is None.")

        self._stream = stream
        self._local_mesg_defs = {}
        self._developer_data_defs = {}
        self._messages = {}
        self._accumulator = Accumulator()

        self._fields_with_subfields = []
        self._fields_to_expand = []

        self._mesg_listener = None
        self._apply_scale_and_offset = True
        self._convert_timestamps_to_datetimes = True
        self._convert_types_to_strings = True
        self._expand_sub_fields = True
        self._expand_components = True
        self._merge_heart_rates = True


    def is_fit(self):
        '''Returns whether the file is a valid fit file.'''
        try:
            file_header_size = self._stream.peek_byte()
            if file_header_size != 14 and file_header_size != 12:
                return False

            if self._stream.get_length() < (file_header_size + _CRCSIZE):
                return False

            # TODO make sure this works with chained files (add offset)
            data_type = self._stream.slice(8, 12)
            if data_type.decode() != ".FIT":
                return False

        except:
            return False

        return True

    def check_integrity(self):
        '''Returns whether the integrity of the file is good or not.'''
        try:
            if self.is_fit() is False:
                return False

            file_header = self.read_file_header()

            if file_header.header_size + file_header.data_size + _CRCSIZE > self._stream.get_length():
                return False

            if file_header.header_size is 14 and file_header.header_crc != CrcCalculator.calculate_crc(self._stream.slice(0, 12), 0, 12):
                return False

            file_crc = CrcCalculator.calculate_crc(self._stream.read_bytes(file_header.file_total_size),0, file_header.file_total_size)
            crc_from_file = self._stream.read_byte() + (self._stream.read_byte() << 8)
            if crc_from_file != file_crc:
                return False

        except:
            return False

        return True

    def read(self, apply_scale_and_offset = True,
                convert_datetimes_to_dates = True,
                convert_types_to_strings = True,
                expand_sub_fields = True,
                expand_components = True,
                merge_heart_rates = True,
                mesg_listener = None):
        '''Reads the entire contents of the fit file and returns the decoded messages'''
        print("------------Begin------------------") # DEBUG STATEMENT
        self._apply_scale_and_offset = apply_scale_and_offset
        self._convert_timestamps_to_datetimes = convert_datetimes_to_dates
        self._convert_types_to_strings = convert_types_to_strings
        self._expand_sub_fields = expand_sub_fields
        self._expand_components = expand_components
        self._merge_heart_rates = merge_heart_rates
        self._mesg_listener = mesg_listener

        self._local_mesg_defs = {}
        self._developer_data_defs = {}
        self._messages = {}

        errors = []
        try:
            if self._merge_heart_rates and (not self._apply_scale_and_offset or not self._expand_components):
                self.__raise_error("merge_heart_rates requires both apply_scale_and_offset and expand_components to be enabled!")


            self._stream.reset()
            if self.is_fit() is False:
                self.__raise_error("The file is not a valid fit file")

            print("Total Size of the Stream/File: ", self._stream.get_length())  # DEBUG STATEMENT
            while self._stream.position() < self._stream.get_length():
                self.__decode_next_file()

            if self._merge_heart_rates is True and 'hr_mesgs' in self._messages:
                hr_mesg_utils.merge_heart_rates(self._messages['hr_mesgs'], self._messages['record_mesgs'])

        except Exception as error:
            errors.append(error)

        finally:
            return self._messages, errors

    def __decode_next_file(self):
        position = self._stream.position()

        if self.is_fit() is False:
            self.__raise_error("The file is not a fit file.")

        self._stream.set_crc_calculator(CrcCalculator())

        file_header = self.read_file_header(False)

        # Read data definitions and messages
        while self._stream.position() < (position + file_header.header_size + file_header.data_size):
            self.__decode_next_record()

        # Check the CRC
        calculated_crc = self._stream.get_crc_caclulator().get_crc()
        crc = self._stream.read_unint_16()

        if crc != calculated_crc:
            self.__raise_error("CRC Error")

    def __decode_next_record(self):
        record_header = self._stream.peek_byte()

        if record_header & _MESG_DEFINITION_MASK == _MESG_HEADER_MASK:
            self.__decode_message()

        if record_header & _MESG_DEFINITION_MASK == _MESG_DEFINITION_MASK:
            self.__decode_mesg_def()

    def __decode_mesg_def(self):
        record_header = self._stream.read_byte()

        struct_format_string = ''
        mesg_def = {}
        mesg_def["record_header"] = record_header
        mesg_def["local_mesg_num"] = record_header & _LOCAL_MESG_NUM_MASK
        mesg_def["reserved"] = self._stream.read_byte()

        mesg_def["architecture"] = self._stream.read_byte()
        mesg_def["endianness"] = Endianness.LITTLE if mesg_def["architecture"] == 0 else Endianness.BIG
        struct_format_string += '>' if mesg_def["endianness"] == Endianness.BIG else '<'

        mesg_def["global_mesg_num"] = self._stream.read_unint_16(mesg_def["endianness"])
        mesg_def["num_fields"] = self._stream.read_byte()
        mesg_def["field_definitions"] = []
        mesg_def["developer_field_defs"] = []
        mesg_def["message_size"] = 0
        mesg_def["developer_data_size"] = 0

        for i in range(mesg_def["num_fields"]):
            field_definition = {
                "field_id": self._stream.read_byte(),
                "size": self._stream.read_byte(),
                "base_type": self._stream.read_byte(),
            }

            if field_definition["base_type"] not in FIT.BASE_TYPE_DEFINITIONS:
                self.__raise_error("Invalid field definition base type")

            num_field_elements = int(field_definition["size"] / FIT.BASE_TYPE_DEFINITIONS[field_definition["base_type"]]["size"])
            field_definition["num_field_elements"] = num_field_elements
            struct_format_string += str(num_field_elements) if num_field_elements > 1 else ''

            struct_format_string += FIT.BASE_TYPE_DEFINITIONS[field_definition["base_type"]]["type_code"]
            mesg_def["struct_format_string"] = struct_format_string
            mesg_def["field_definitions"].append(field_definition)
            mesg_def["message_size"] += field_definition["size"]

        if record_header & _DEV_DATA_MASK == _DEV_DATA_MASK:
            num_dev_fields = self._stream.read_byte()

            for i in range(num_dev_fields):
                developer_field_definition = {
                    "field_definition_number": self._stream.read_byte(),
                    "size": self._stream.read_byte(),
                    "developer_data_index": self._stream.read_byte(),
                    "endianness": Endianness.LITTLE if mesg_def["architecture"] == 0 else Endianness.BIG
                }

                mesg_def["developer_field_defs"].append(developer_field_definition)
                mesg_def["developer_data_size"] += developer_field_definition["size"]

        if mesg_def["global_mesg_num"] in Profile['messages']:
            message_profile = Profile['messages'][mesg_def["global_mesg_num"]]
        else:
            message_profile = {
                "name": str(mesg_def["global_mesg_num"]),
                "messages_key": str(mesg_def["global_mesg_num"]),
                "num": mesg_def["global_mesg_num"],
                'fields': {}
            }

        #TODO add option for unknown data

        # Add the profile to the local message definition
        self._local_mesg_defs[mesg_def["local_mesg_num"]] = {**mesg_def, **message_profile}

        messages_key = message_profile['messages_key'] if 'messages_key' in message_profile else None
        if message_profile is not None and messages_key not in self._messages:
            self._messages[messages_key] = []

    def __decode_message(self):
        record_header = self._stream.read_byte()

        local_mesg_num = record_header & _LOCAL_MESG_NUM_MASK
        if local_mesg_num in self._local_mesg_defs:
            mesg_def = self._local_mesg_defs[local_mesg_num]
        else:
            self.__raise_error("Invalid local message number")

        messages_key = mesg_def['messages_key']

        # Decode regular message
        message = {}
        self._fields_to_expand = []
        self._fields_with_subfields = []

        message = self.__read_message(mesg_def)

        developer_fields = {}

        # Decode developer data if it exists
        if len(mesg_def["developer_field_defs"]) > 0:

            for developer_field_def in mesg_def['developer_field_defs']:
                field_profile = self.__lookup_developer_data_field(developer_field_def)
                if field_profile is None:
                    # If there is not a field definition, then read past the field data.
                    self._stream.read_bytes(developer_field_def['size'])
                    continue

                struct_format_string = self.__build_dev_data_struct_string(developer_field_def, field_profile)
                field_value = self.__read_raw_value(developer_field_def['size'], struct_format_string)

                if field_profile['fit_base_type_id'] == FIT.BASE_TYPE['STRING']:
                    field_value = util._convert_string(field_value)
                #NOTE possible point to scrub invalids????

                if field_value is not None:
                    developer_fields[field_profile['key']] = field_value

        if mesg_def['global_mesg_num'] == Profile['mesg_num']['DEVELOPER_DATA_ID']:
            self.__add_developer_data_id_to_profile(message)

        elif mesg_def['global_mesg_num'] == Profile['mesg_num']['FIELD_DESCRIPTION']:
            message['key'] = len(self._messages[messages_key])
            self.__add_field_description_to_profile(message)

        else:
            message = self.__apply_profile(mesg_def, message)

        self.__clean_message(message)

        if len(developer_fields) is not 0:
            message['developer_fields'] = developer_fields

        # Append decoded message
        self._messages[messages_key].append(message)

        if self._mesg_listener is not None:
            self._mesg_listener(mesg_def['global_mesg_num'], message)


    def __read_message(self, mesg_def):
        message = {}
        raw_values = self.__read_raw_values(mesg_def["message_size"], mesg_def["struct_format_string"])

        index = 0
        for field in mesg_def['field_definitions']:
            base_type_definition = FIT.BASE_TYPE_DEFINITIONS[field["base_type"]]
            invalid = base_type_definition["invalid"]
            num_elements = field["num_field_elements"]

            field_id = field["field_id"]
            field_profile = mesg_def['fields'][field_id] if field_id in mesg_def['fields'] else None
            field_name = field_profile['name'] if field_id in mesg_def['fields'] else field_id

            if field_profile is not None and 'has_components' in field_profile:
                convert_invalids_to_none = not field_profile['has_components']
            else:
                convert_invalids_to_none = True

            field_value = None

            # Fields with strings or string arrays
            if base_type_definition['type'] == FIT.BASE_TYPE["STRING"]:
                field_value = util._convert_string(raw_values[index])

            # Fields with an array of values
            elif num_elements > 1:
                field_value = []
                for i in range(num_elements):
                    raw_value = raw_values[index + i] if raw_values[index + i] != invalid or not convert_invalids_to_none else None
                    field_value.append(raw_value)
                if self.__is_array_all_none(field_value) is True:
                    field_value = None

            # Fields with a single value
            else:
                if raw_values[index] != invalid or not convert_invalids_to_none:
                    field_value = raw_values[index]

            if field_value is not None:
                message[field_name] = {
                'raw_field_value': field_value,
                'field_definition_number': field_id
                }

                if field_profile and len(field_profile['sub_fields']) > 0:
                    self._fields_with_subfields.append(field_name)

                if field_profile and field_profile['has_components'] is True:
                    self._fields_to_expand.append(field_name)

                if field_profile and field_profile['is_accumulated'] is True:
                    self._accumulator.add(mesg_def['global_mesg_num'], field_id, field_value)

            index += num_elements if base_type_definition['type'] != FIT.BASE_TYPE["STRING"] else 1

        return message

    def __apply_profile(self, mesg_def: dict, raw_message: dict):
        message = raw_message

        if self._expand_sub_fields is True:
            self.__expand_sub_fields(mesg_def['global_mesg_num'], message)

        self.__expand_components(mesg_def['global_mesg_num'], message, mesg_def['fields'], mesg_def)

        self.__transform_values(message, mesg_def)

        return message

    def __transform_values(self, message, mesg_def):
        for field in message:
            if 'is_expanded_field'in message[field] and message[field]['is_expanded_field'] is True:
                continue

            field_name = field
            field_id = message[field]['field_definition_number']
            field_profile = mesg_def['fields'][field_id] if field_id in mesg_def['fields'] else None
            field_type = field_profile['type'] if field_id in mesg_def['fields'] else field_id

            is_sub_field = message[field]['is_sub_field'] if 'is_sub_field' in message[field] else False
            if is_sub_field:
                field_profile = self.__get_subfield_profile(field_profile, field_name)
                field_type = field_profile['type'] if field_id in mesg_def['fields'] else field_id

            field_value = message[field_name]['raw_field_value']
            # Optional data operations
            if self._convert_types_to_strings is True:# and field_type in Profile['types']:
                field_value = self.__convert_type_to_string(field_type, message[field_name]['raw_field_value'])

            if self._apply_scale_and_offset is True and field_type in FIT.NUMERIC_FIELD_TYPES:
                field_value = self.__apply_scale_and_offset(field_profile, message[field_name]['raw_field_value'])

            if self._convert_timestamps_to_datetimes is True and field_type == 'date_time':
                field_value = util.convert_timestamp_to_datetime(message[field_name]['raw_field_value'])

            message[field_name]['field_value'] = field_value
        return

    def __expand_components(self, mesg_num, message, fields, mesg_def):
        if self._expand_components is False or len(self._fields_to_expand) == 0:
            return

        mesg = {}

        while len(self._fields_to_expand) > 0:
            field = self._fields_to_expand.pop()


            raw_field_value = message[field]['raw_field_value']
            field_definition_number = message[field]['field_definition_number']
            if field_definition_number not in mesg_def['fields']:
                continue

            field_profile = mesg_def['fields'][field_definition_number]

            if 'is_sub_field' in message[field] and  message[field]['is_sub_field'] is True:
                field_profile = self.__get_subfield_profile(field_profile, field)

            base_type = FIT.FIELD_TYPE_TO_BASE_TYPE[field_profile['type']] if field_profile['type'] in FIT.FIELD_TYPE_TO_BASE_TYPE else None

            if field_profile is None or field_profile['has_components'] is False or base_type is None:
                continue

            if util._only_invalid_values(raw_field_value, FIT.BASE_TYPE_DEFINITIONS[base_type]['invalid']) is True:
                continue

            bitstream = BitStream(raw_field_value, base_type)

            for i in range(len(field_profile['components'])):
                target_field = fields[field_profile['components'][i]]
                if target_field['name'] not in mesg:
                    base_type = FIT.FIELD_TYPE_TO_BASE_TYPE[target_field['type']]
                    invalid_value = FIT.BASE_TYPE_DEFINITIONS[base_type]['invalid']

                    mesg[target_field['name']] = {
                        'field_value': [],
                        'raw_field_value': [],
                        'field_definition_number': target_field['num'],
                        'is_expanded_field': True,
                        'invalid': invalid_value
                    }

                if bitstream.bits_available() < field_profile['bits'][i]:
                    break

                value = bitstream.read_bits(field_profile['bits'][i])

                value = self._accumulator.accumulate(mesg_num, target_field['num'], value, field_profile['bits'][i])

                mesg[target_field['name']]['raw_field_value'].append(value)
                if value == invalid_value:
                    mesg[target_field['name']]['field_value'].append(None)
                else:
                    value = value / field_profile['scale'][i] - field_profile['offset'][i]
                    mesg[target_field['name']]['field_value'].append(value)

                if target_field['has_components'] is True:
                    self._fields_to_expand.append(target_field['name'])

                if bitstream.has_bits_available() is False:
                    break

        ##clean message TODO make this take both raw and field
        for field in mesg:
            message[field] = mesg[field]

    def __expand_sub_fields(self, global_mesg_num, message):
        if self._expand_sub_fields is False or len(self._fields_with_subfields) == 0:
            return

        # Save the original fields for iteration before expanding sub fields.
        for field in self._fields_with_subfields:
            if message[field]['field_definition_number'] in Profile['messages'][global_mesg_num]['fields']:
                field_profile = Profile['messages'][global_mesg_num]['fields'][message[field]['field_definition_number']]
            else:
                continue

            if len(field_profile['sub_fields']) > 0:
                self.__expand_sub_field(message, field_profile)

    def __expand_sub_field(self, message, field_profile):
        for sub_field in field_profile['sub_fields']:
            for map_item in sub_field['map']:
                reference_field_profile = message[map_item['name']] if map_item['name'] in message else None

                if reference_field_profile is None:
                    continue

                if reference_field_profile['raw_field_value'] == map_item['raw_value']:
                    message[sub_field['name']] = copy.deepcopy(message[field_profile['name']])
                    message[sub_field['name']]['is_sub_field'] = True

                    if sub_field['has_components'] is True:
                        self._fields_to_expand.append(sub_field['name'])

                    break

    def __get_subfield_profile(self, field_profile, name):
        return next(sub_field for sub_field in field_profile['sub_fields'] if sub_field['name'] == name) or {}

    def __convert_type_to_string(self, field_type, raw_field_value):
        try:
            if field_type in Profile['types']:
                types = Profile['types'][field_type]
            else:
                return raw_field_value

            field_value = raw_field_value

            if isinstance(raw_field_value, list):
                for i in range(len(raw_field_value)):
                    field_value[i] = types[str(raw_field_value[i])] if str(raw_field_value[i]) in types else raw_field_value[i]
                return field_value

            return types[str(raw_field_value)] if str(raw_field_value) in types else field_value
        except:
            return raw_field_value

    def __apply_scale_and_offset(self, field_profile, raw_field_value):

        if self._apply_scale_and_offset is False:
            return raw_field_value

        if raw_field_value is None:
            return raw_field_value

        if len(field_profile['scale']) > 1:
            return raw_field_value

        scale = field_profile['scale'][0] if field_profile['scale'] else 1
        offset = field_profile['offset'][0] if field_profile['offset'] else 0

        try:

            field_value = raw_field_value

            if isinstance(raw_field_value, list):
                for i in range(len(raw_field_value)):
                    field_value[i] = (raw_field_value[i] / scale - offset) if raw_field_value[i] is not None else None
                return field_value

            return (raw_field_value / scale) - offset
        except:
            return raw_field_value


    def __add_developer_data_id_to_profile(self, message):
        if message is None or message['developer_data_index'] is None or message['developer_data_index']['raw_field_value'] == 0xFF:
            return

        self._developer_data_defs[message['developer_data_index']['raw_field_value']] = {
            'developer_data_index': message['developer_data_index']['raw_field_value'],
            'developer_id': message['developer_id']['raw_field_value'] if 'developer_id' in message else None,
            'application_id': message['application_id']['raw_field_value'] if 'application_id' in message else None,
            'manufacturer_id': message['manufacturer_id']['raw_field_value'] if 'manufacturer_id' in message else None,
            'application_version': message['application_version']['raw_field_value'] if 'application_version' in message else None,
            'fields': []
        }

    def __add_field_description_to_profile(self, message):

        if message is None or message['developer_data_index'] is None or message['developer_data_index']['raw_field_value'] == 0xFF:
            return

        if self._developer_data_defs[message['developer_data_index']['raw_field_value']] is None:
            return

        if message["fit_base_type_id"] is not None:
            base_type_code = FIT.BASE_TYPE_DEFINITIONS[message["fit_base_type_id"]['raw_field_value']]["type_code"]
        else:
            base_type_code = None

        self._developer_data_defs[message['developer_data_index']['raw_field_value']]['fields'].append({
            'developer_data_index': message['developer_data_index']['raw_field_value'],
            'field_definition_number': message['field_definition_number']['raw_field_value'],
            'fit_base_type_id': message['fit_base_type_id']['raw_field_value'] if 'fit_base_type_id' in message else None,
            'base_type_code': base_type_code,
            'name': message['name']['raw_field_value'] if 'name' in message else None,
            'array': message['array']['raw_field_value'] if 'array' in message else None,
            'components': message['components']['raw_field_value'] if 'components' in message else None,
            'scale': message['scale']['raw_field_value'] if 'scale' in message else None,
            'offset': message['offset']['raw_field_value'] if 'offset' in message else None,
            'units': message['units']['raw_field_value'] if 'units' in message else None,
            'bits': message['bits']['raw_field_value'] if 'bits' in message else None,
            'accumulate': message['accumulate']['raw_field_value'] if 'accumulate' in message else None,
            'ref_field_name': message['ref_field_name']['raw_field_value'] if 'ref_field_name' in message else None,
            'ref_field_value': message['ref_field_value']['raw_field_value'] if 'ref_field_value' in message else None,
            'fit_base_unit_id': message['fit_base_unit_id']['raw_field_value'] if 'fit_base_unit_id' in message else None,
            'native_mesg_num': message['native_mesg_num']['raw_field_value'] if 'native_mesg_num' in message else None,
            'native_field_num': message['native_field_num']['raw_field_value'] if 'native_field_num' in message else None,
            'key': message['key']
        })

    def __build_dev_data_struct_string(self, developer_field_def: dict, field_profile: dict):
        struct_format_string = "<" if developer_field_def['endianness'] == Endianness.LITTLE else ">"
        invalid_value = FIT.BASE_TYPE_DEFINITIONS[field_profile['fit_base_type_id']]['invalid']
        base_type_code = field_profile['base_type_code']
        base_type_size = FIT.BASE_TYPE_DEFINITIONS[field_profile['fit_base_type_id']]['size']
        num_elements = int(developer_field_def["size"] / base_type_size)

        field_profile['num_elements'] = num_elements
        field_profile['invalid'] = invalid_value

        struct_format_string += str(num_elements) + base_type_code

        return struct_format_string

    def __lookup_developer_data_field(self, developer_field_def):
        try:
            for field in self._developer_data_defs[developer_field_def['developer_data_index']]['fields']:
                if field['field_definition_number'] == developer_field_def['field_definition_number']:
                    return field

            return None

        except:
            return None

    def __clean_message(self, message):
        if message is not None:
            for field in message:
                if isinstance(message[field], dict) and 'raw_field_value' in message[field]:
                    message[field] = message[field]['field_value'] if 'field_value' in message[field] else message[field]['raw_field_value']
                if isinstance(message[field], list) and len(message[field]) == 1:
                    message[field] = message[field][0]

    def __read_raw_values(self, message_size, struct_format_string):
        return self._stream.read_and_unpack(message_size, struct_format_string)

    def __read_raw_value(self, message_size, struct_format_string):
        field_value = self._stream.read_and_unpack(message_size, struct_format_string)
        return field_value if len(field_value) > 1 else field_value[0]

    def __is_array_all_none(self, array):
        for i in array:
            if i is not None:
                return False
        return True

    def __raise_error(self, error = ""):
        position = self._stream.position()
        message = "FIT Runtime Error at byte: " + str(position) + " " + error
        raise RuntimeError(message)

    def read_file_header(self, reset=True):
        '''Reads the file's header and returns its parameters.'''
        starting_position = self._stream.position()

        class FileHeader(object):
            '''A class that holds a .fit File Headers parameters.'''
            header_size = self._stream.read_byte()
            protocol_version = self._stream.read_byte()
            profile_version = self._stream.read_unint_16("big") / 100
            data_size = self._stream.read_unint_32("little")
            data_type = self._stream.read_string(4)
            header_crc = 0
            file_total_size = header_size + data_size

        if FileHeader.header_size is 14:
            FileHeader.header_crc = self._stream.read_unint_16("little")

        if reset is True:
            self._stream.seek(starting_position)

        return FileHeader

    def get_num_messages(self):
        '''Returns the total number of messages successfully decoded from the file(s)'''
        num_messages = 0
        for message in self._messages:
            num_messages += len(self._messages[message])
        return num_messages