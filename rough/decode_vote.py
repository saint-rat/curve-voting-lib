#!/usr/bin/env python3
import argparse
import sys

from eth_abi import decode
from eth_utils import keccak, to_checksum_address


OWNERSHIP_VOTING = "0xE478de485ad2fe566d49342Cbd03E49ed7DB3356"

CONTROLLERS = {
    "0x745422bf49f3f6e4a8e12e4abd19339e7910f8c9": ("wstETH/WETH", 18, "WETH"),
    "0x9fc15ac3ef97093832f49b7997a58e29b49c56de": ("WBTC/USDC", 6, "USDC"),
    "0xb5ec7a3d591877a66be4f3eafdc4205e98a1bcaa": ("wstETH/USDC", 6, "USDC"),
}


def selector(signature: str) -> bytes:
    return keccak(text=signature)[:4]


def decode_function(data: bytes, signature: str, types: list[str]):
    expected = selector(signature)
    if data[:4] != expected:
        raise ValueError(
            f"Expected {signature} selector 0x{expected.hex()}, "
            f"got 0x{data[:4].hex()}"
        )
    return decode(types, data[4:])


def format_units(value: int, decimals: int) -> str:
    whole, remainder = divmod(value, 10**decimals)
    if remainder == 0:
        return f"{whole:,}"
    fraction = str(remainder).rjust(decimals, "0").rstrip("0")
    return f"{whole:,}.{fraction}"


def print_message(index: int, target: str, data: bytes) -> None:
    target = to_checksum_address(target)
    method_id = data[:4]
    print(f"    Message {index}")
    print(f"      Target: {target}")

    if method_id == selector("set_borrow_cap(address,uint256)"):
        controller, borrow_cap = decode_function(
            data,
            "set_borrow_cap(address,uint256)",
            ["address", "uint256"],
        )
        controller = to_checksum_address(controller)
        print("      Function: set_borrow_cap(address,uint256)")
        print(f"      Controller: {controller}")
        print(f"      Borrow cap (raw): {borrow_cap}")

        market = CONTROLLERS.get(controller.lower())
        if market:
            name, decimals, symbol = market
            print(f"      Market: {name}")
            print(f"      Borrow cap: {format_units(borrow_cap, decimals)} {symbol}")

    elif method_id == selector("set_admin_percentage(address,uint256)"):
        controller, percentage = decode_function(
            data,
            "set_admin_percentage(address,uint256)",
            ["address", "uint256"],
        )
        controller = to_checksum_address(controller)
        print("      Function: set_admin_percentage(address,uint256)")
        print(f"      Controller: {controller}")
        print(f"      Admin percentage (raw): {percentage}")
        print(f"      Admin percentage: {percentage / 10**16:g}%")

    else:
        print(f"      Unknown selector: 0x{method_id.hex()}")
        print(f"      Data: 0x{data.hex()}")


def decode_vote(calldata: bytes, voting_contract: str) -> None:
    script, metadata, cast_vote, executes_if_decided = decode_function(
        calldata,
        "newVote(bytes,string,bool,bool)",
        ["bytes", "string", "bool", "bool"],
    )

    print("Vote creation transaction")
    print("  Chain: Ethereum (1)")
    print(f"  To: {to_checksum_address(voting_contract)}")
    print("  Value: 0")
    print("  Function: newVote(bytes,string,bool,bool)")
    print(f"  Metadata: {metadata!r}")
    print(f"  Cast vote: {cast_vote}")
    print(f"  Execute if decided: {executes_if_decided}")
    print(f"  EVM script length: {len(script)} bytes")

    if script[:4] != bytes.fromhex("00000001"):
        raise ValueError(f"Unsupported Aragon script ID: 0x{script[:4].hex()}")

    print("  Aragon script ID: 0x00000001 (CallsScript)")
    offset = 4
    record_index = 0

    while offset < len(script):
        record_index += 1
        if offset + 24 > len(script):
            raise ValueError("Truncated Aragon call record")

        agent = to_checksum_address(script[offset : offset + 20])
        call_length = int.from_bytes(script[offset + 20 : offset + 24], "big")
        call_start = offset + 24
        call_end = call_start + call_length
        if call_end > len(script):
            raise ValueError("Aragon call record exceeds script length")

        agent_calldata = script[call_start:call_end]
        broadcaster, eth_value, broadcaster_calldata = decode_function(
            agent_calldata,
            "execute(address,uint256,bytes)",
            ["address", "uint256", "bytes"],
        )

        print(f"\n  CallsScript record {record_index}")
        print(f"    Agent: {agent}")
        print(f"    Agent calldata length: {call_length} bytes")
        print("    Function: execute(address,uint256,bytes)")
        print(f"    Target broadcaster: {to_checksum_address(broadcaster)}")
        print(f"    ETH value: {eth_value}")

        messages, gas_limit = decode_function(
            broadcaster_calldata,
            "broadcast((address,bytes)[],uint32)",
            ["(address,bytes)[]", "uint32"],
        )
        print("    Broadcaster function: broadcast((address,bytes)[],uint32)")
        print(f"    Optimism gas limit: {gas_limit}")
        print(f"    Message count: {len(messages)}")

        for index, (target, message_data) in enumerate(messages, 1):
            print_message(index, target, message_data)

        offset = call_end

    if offset != len(script):
        raise ValueError("Aragon script was not consumed exactly")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Decode a Curve DAO vote-creation transaction offline."
    )
    parser.add_argument(
        "calldata",
        nargs="?",
        help="0x-prefixed newVote calldata; read from stdin when omitted",
    )
    parser.add_argument(
        "--to",
        default=OWNERSHIP_VOTING,
        help=f"voting contract address (default: {OWNERSHIP_VOTING})",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    raw = args.calldata if args.calldata is not None else sys.stdin.read()
    raw = raw.strip()
    if not raw:
        raise SystemExit("No calldata supplied")

    try:
        calldata = bytes.fromhex(raw.removeprefix("0x"))
        decode_vote(calldata, args.to)
    except (ValueError, TypeError) as error:
        raise SystemExit(f"Decode failed: {error}") from error


if __name__ == "__main__":
    main()
