"""BLE GATT peripheral for MedAI Flutter app.

Delegates server implementation to `nodes.ble_receiver_node`.
"""
import argparse
from nodes.ble_receiver_node import (
    BLEReceiverNode,
    CommandCharacteristic,
    ResponseCharacteristic,
    _get_supervisor,
    _supervisor,
    start_ble_server,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--legacy-advertising",
        action="store_true",
        help="Use btmgmt advertising for Raspberry Pi kernels affected by the 6.18 BLE regression.",
    )
    args = parser.parse_args()
    start_ble_server(legacy_advertising=args.legacy_advertising)


if __name__ == "__main__":
    main()
