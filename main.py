import json
from collections import OrderedDict


class ISO8583Parser:
    """
    A configuration-driven ISO 8583 message parser that handles a prepended header.
    This version reads an 'iso8583.json' file to understand the structure of the message
    and breaks down a raw message string into its individual fields.
    """

    def __init__(self, config_file_path: str):
        """
        The constructor for the parser. Its main job is to load the field definitions.

        When you create an instance of this class (e.g., parser = ISO8583Parser(...)),
        this method is automatically called.

        Args:
            config_file_path (str): The file path to the JSON configuration file.
        """
        try:
            with open(config_file_path, 'r') as f:
                self.config = json.load(f)
            print("✅ ISO 8583 configuration loaded successfully.")
        except FileNotFoundError:
            print(f"❌ ERROR: Configuration file not found at '{config_file_path}'")
            self.config = None
        except json.JSONDecodeError:
            print(f"❌ ERROR: Could not decode JSON from '{config_file_path}'")
            self.config = None

    def _hex_to_binary(self, hex_string: str) -> str:
        """
        A private helper method to convert a hexadecimal string into a binary string.
        The leading underscore `_` is a Python convention for internal-use methods.

        Args:
            hex_string (str): A string of hexadecimal characters (e.g., "723A...").

        Returns:
            str: A zero-padded binary string of 64 characters (e.g., "01110010...").
        """
        # int(hex_string, 16) converts the hex string to its integer equivalent.
        # bin(...) converts that integer to a binary string (e.g., "0b111...").
        # [2:] slices off the "0b" prefix from the start of the string.
        # .zfill(64) pads the string with leading zeros until it is 64 characters long.
        # This padding is crucial to ensure the bitmap is always the correct length.
        return bin(int(hex_string, 16))[2:].zfill(64)

    def _get_active_fields(self, hex_bitmap: str) -> list[int]:
        """
        A private helper method that reads a hex bitmap and determines which fields are present.

        Args:
            hex_bitmap (str): The 16-character hexadecimal primary or secondary bitmap.

        Returns:
            list[int]: A list of integers representing the active field numbers.
        """
        # First, convert the 16-char hex bitmap into a 64-char binary string.
        binary_bitmap = self._hex_to_binary(hex_bitmap)

        active_fields = []
        # enumerate gives us both the index (0-63) and the value ('0' or '1') for each bit.
        for index, bit in enumerate(binary_bitmap):
            # If the bit is '1', it means the corresponding field is active.
            if bit == '1':
                # Fields are 1-based (1-64), while the list index is 0-based (0-63).
                # So, we add 1 to the index to get the correct field number.
                active_fields.append(index + 1)
        return active_fields

    def parse(self, iso_message: str) -> dict:
        """
        The main engine of the parser. It takes a complete ISO message string and
        chops it into its component parts based on the loaded configuration.

        Args:
            iso_message (str): The raw, single-line string containing the entire ISO message.

        Returns:
            dict: An ordered dictionary containing the structured, parsed data.
        """
        # Safety check: If the configuration wasn't loaded, we can't do anything.
        if not self.config:
            print("❌ Cannot parse message: Configuration is not loaded.")
            return {}

        parsed_data = OrderedDict()
        current_pos = 0

        try:
            # --- HEADER PARSING ---
            parsed_data['iso_identifier'] = iso_message[current_pos: current_pos + 3]
            current_pos += 3
            parsed_data['proprietary_header'] = iso_message[current_pos: current_pos + 9]
            current_pos += 9

            # --- STANDARD ISO 8583 MESSAGE PARSING ---
            mti = iso_message[current_pos: current_pos + 4]
            current_pos += 4
            parsed_data['mti'] = mti

            primary_bitmap_hex = iso_message[current_pos: current_pos + 16]
            current_pos += 16

            active_fields = self._get_active_fields(primary_bitmap_hex)
            parsed_data['bitmap'] = OrderedDict([('primary_hex', primary_bitmap_hex)])

            # Check if Field 1 is active, which indicates a secondary bitmap is present.
            if 1 in active_fields:
                secondary_bitmap_hex = iso_message[current_pos: current_pos + 16]
                current_pos += 16
                secondary_fields = self._get_active_fields(secondary_bitmap_hex)
                # Add the secondary fields (65-128) to our list of active fields.
                active_fields.extend([field + 64 for field in secondary_fields])
                # We don't need to parse Field 1 as data, so we remove it.
                active_fields.remove(1)
                parsed_data['bitmap']['secondary_hex'] = secondary_bitmap_hex

            parsed_data['active_fields'] = sorted(active_fields)
            parsed_data['fields'] = OrderedDict()

            # We sort the active fields to ensure we parse them in the correct order.
            for field_num in sorted(active_fields):
                field_key = str(field_num)
                if field_key not in self.config:
                    parsed_data['parsing_error'] = f"No configuration found for Field {field_num}."
                    return parsed_data

                field_config = self.config[field_key]
                field_type = field_config.get("type", "FIXED")

                # Get the raw data slice based on its type from the config file.
                if field_type == "FIXED":
                    length = field_config["length"]
                    data = iso_message[current_pos: current_pos + length]
                    current_pos += length

                elif field_type == "VARIABLE":
                    len_digits = field_config["length_digits"]
                    len_indicator_str = iso_message[current_pos: current_pos + len_digits]
                    if not len_indicator_str.isdigit():
                        parsed_data[
                            'parsing_error'] = f"Invalid length indicator for Field {field_num}. Expected digits, got '{len_indicator_str}'."
                        return parsed_data
                    current_pos += len_digits
                    data_length = int(len_indicator_str)

                    data = iso_message[current_pos: current_pos + data_length]
                    current_pos += data_length
                else:
                    parsed_data['parsing_error'] = f"Unknown field type '{field_type}' for Field {field_num}."
                    return parsed_data

                # Step 2: Directly assign the data as-is.
                parsed_data['fields'][field_num] = {
                    "description": field_config["description"],
                    "length": len(data),
                    "value": data  # Assign the raw data slice directly
                }

            # This check must be OUTSIDE the for loop
            if current_pos < len(iso_message):
                remaining_data = iso_message[current_pos:]
                parsed_data['parsing_error'] = (
                    "MISMATCH: Parser finished, but there is unparsed data remaining. "
                    f"This means the data string is longer than the bitmap specifies. "
                    f"Remaining data: '{remaining_data}'"
                )

        except (IndexError, ValueError) as e:
            parsed_data['parsing_error'] = (
                "CRITICAL MISMATCH: Parser crashed while processing. "
                "This usually means the data string is shorter than the bitmap specifies. "
                f"System error: {e}"
            )

        return parsed_data


def display_parsed_message(parsed_data: dict):
    """
    Prints the parsed ISO 8583 message in a human-readable format.
    """
    if not parsed_data:
        return

    print("\n--- ISO 8583 Parsed Message ---")

    iso_id = parsed_data.get('iso_identifier', 'N/A')
    prop_header = parsed_data.get('proprietary_header', 'N/A')

    print("\n[Message Header]")
    print(f"  ISO Identifier:     {iso_id}")
    print(f"  Proprietary Header: {prop_header}")

    print("\n[ISO Core Message]")
    mti = parsed_data.get('mti', 'N/A')
    print(f"  MTI: {mti}")

    if 'bitmap' in parsed_data:
        print("\n  [Bitmap Information]")
        print(f"    Primary (Hex):   {parsed_data['bitmap'].get('primary_hex', 'N/A')}")
        if 'secondary_hex' in parsed_data['bitmap']:
            print(f"    Secondary (Hex): {parsed_data['bitmap']['secondary_hex']}")
        print(f"    Active Fields:   {parsed_data.get('active_fields', '[]')}")

    if 'fields' in parsed_data and parsed_data['fields']:
        print("\n  [Data Fields]")
        print("  " + "-" * 70)
        print(f"  {'Field':<5} {'Description':<40} {'Len':<5} {'Value'}")
        print("  " + "-" * 70)

        for field_num, details in parsed_data['fields'].items():
            print(f"  {field_num:<5} {details['description']:<40} {details['length']:<5} {details['value']}")
        print("  " + "-" * 70)

    if 'parsing_error' in parsed_data:
        print("\n" + "!" * 25 + " PARSING ERROR " + "!" * 25)
        print(f"  {parsed_data['parsing_error']}")
        print("!" * 70)

    print("\n")


# --- Main Execution Block (Simplified) ---
if __name__ == "__main__":
    parser = ISO8583Parser('iso8583.json')

    if parser.config:
        print("\n--- Interactive ISO 8583 Parser ---")
        print("This parser treats all fields as plain text.")
        print("Paste a full ISO message string and press Enter.")
        print("Type 'exit' or 'quit' to close the program.")

        print("\nSample ISO Message: ISO0250000700100B238C68128A18018000000000000000C0000000000000050000626234859721245234859062606265542271001061156416200000371234567890123456D2807221000000000058951771572124566871693PUBLIC BANK BERHAD    IPOH            MY027PETRON KINDING             458016PBB PRO2+0000000019MBB2PRO200000000000012P BICIB24 1003820351771572124500000000000000000000000")

        while True:
            iso_string_to_parse = input("\nEnter ISO Message > ").strip()

            if iso_string_to_parse.lower() in ['exit', 'quit']:
                break

            if not iso_string_to_parse:
                continue

            parsed_iso_message = parser.parse(iso_string_to_parse)
            display_parsed_message(parsed_iso_message)

        print("\nParser exited. Goodbye!")
