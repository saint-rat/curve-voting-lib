from __future__ import annotations
from contextlib import contextmanager, ExitStack
from dataclasses import dataclass, field
import os
from typing import Any, Optional, TYPE_CHECKING

import boa
import logging
from dotenv import load_dotenv
from hexbytes import HexBytes

from voting.config import CONVEX_VOTER_PROXY, DAOParameters
from requests import request
import hashlib
import json
from datetime import datetime
from voting import abi 
from boa.util.abi import abi_decode

from voting.context import (
    get_dao,
    get_xgov_preview,
    use_clean_prepare_calldata,
    use_dao,
    use_prepare_calldata,
    use_xgov_preview,
)
from voting.live_env import LiveEnv

if TYPE_CHECKING:
    from voting.xgov.chains import Chain

logger = logging.getLogger(__name__)
load_dotenv()


@dataclass(frozen=True)
class _DecodedInput:
    type: str
    name: str
    value: Any


@dataclass
class _CapturedCall:
    address: str
    calldata: bytes
    function_name: str
    inputs: list[_DecodedInput]
    xgov_calls: list[_CapturedCall] = field(default_factory=list)
    xgov_chain_id: Optional[int] = None
    xgov_chain_name: Optional[str] = None


class _XGovPreview:
    def __init__(self, chain: Chain, calls: list[_CapturedCall]):
        self.chain = chain
        self.calls = calls
        self.cursor = 0

    def attach(self, broadcaster_call: _CapturedCall) -> None:
        messages = _extract_messages(broadcaster_call)
        calls = self.calls[self.cursor : self.cursor + len(messages)]
        if len(calls) != len(messages):
            raise ValueError("Broadcaster contains more messages than xvote captured")

        for (target, calldata), call in zip(messages, calls):
            if target.lower() != call.address.lower() or calldata != bytes(call.calldata):
                raise ValueError("Broadcaster messages do not match captured xvote calls")

        broadcaster_call.xgov_calls = calls
        broadcaster_call.xgov_chain_id = self.chain.id
        broadcaster_call.xgov_chain_name = self.chain.name or f"Chain {self.chain.id}"
        self.cursor += len(messages)

    def assert_complete(self) -> None:
        if self.cursor != len(self.calls):
            raise ValueError("Not all captured xvote calls were broadcast")


def _capture_call(func, calldata: bytes) -> _CapturedCall:
    try:
        decoded_values = abi_decode(func.signature, calldata[4:])
        inputs = [
            _DecodedInput(abi_input["type"], abi_input["name"], value)
            for abi_input, value in zip(func._abi["inputs"], decoded_values)
        ]
    except Exception:
        inputs = [_DecodedInput("bytes", "calldata", bytes(calldata).hex())]

    return _CapturedCall(
        address=str(func.contract.address),
        calldata=calldata,
        function_name=func.name,
        inputs=inputs,
    )


def _extract_messages(call: _CapturedCall) -> list[tuple[str, bytes]]:
    for decoded_input in call.inputs:
        if (
            decoded_input.name.lstrip("_") == "messages"
            and decoded_input.type == "tuple[]"
        ):
            return [
                (str(target), bytes(calldata))
                for target, calldata in decoded_input.value
            ]
    raise ValueError("Could not find messages in broadcaster calldata")


def _format_value(value):
    if isinstance(value, str):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).hex()
    if isinstance(value, tuple):
        return tuple(_format_value(item) for item in value)
    if isinstance(value, list):
        return [_format_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _format_value(item) for key, item in value.items()}
    return value


def _format_inputs(call: _CapturedCall) -> str:
    inputs = [
        f"('{item.type}', '{item.name}', '{_format_value(item.value)}')"
        for item in call.inputs
    ]
    return f"[{', '.join(inputs)}]"


def _pin_to_ipfs(description: str) -> str:
    # Create cache directory if it doesn't exist
    cache_dir = os.path.expanduser("~/.cache/curve-voting-lib")
    os.makedirs(cache_dir, exist_ok=True)
    cache_file = os.path.join(cache_dir, "ipfs_cache.json")
    
    # Create a hash of the description for cache key
    description_hash = hashlib.sha256(description.encode()).hexdigest()
    
    # Load existing cache
    cache = {}
    if os.path.exists(cache_file):
        try:
            with open(cache_file, 'r') as f:
                cache = json.load(f)
        except (json.JSONDecodeError, IOError):
            logger.warning("Could not load IPFS cache, starting fresh")
            cache = {}
    
    # Check if description is already cached
    if description_hash in cache:
        ipfs_hash = cache[description_hash]
        logger.info(f"Found cached IPFS hash for description.")
        return f"ipfs:{ipfs_hash}"

    pinata_token = os.getenv("PINATA_JWT")
    if not pinata_token:
        raise ValueError("PINATA_JWT environment variable is required")

    # TODO this is a legacy endpoint and should be updated before it breaks
    url = "https://api.pinata.cloud/pinning/pinJSONToIPFS"
    headers = {
        "Authorization": f"Bearer {pinata_token}",
        "Content-Type": "application/json",
    }
    payload = {
        "pinataContent": {"text": description},
        "pinataMetadata": {"name": f"vote_description_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"},
        "pinataOptions": {"cidVersion": 1},
    }

    response = request("POST", url, json=payload, headers=headers)

    if not (200 <= response.status_code < 400):
        logger.error(f"IPFS pinning failed with status {response.status_code}: {response.text}")
        raise Exception(f"Failed to pin to IPFS: HTTP {response.status_code}")
    
    response_data = response.json()
    ipfs_hash = response_data["IpfsHash"]
    logger.info(f"Successfully pinned vote description to IPFS: {ipfs_hash}")
    
    # Cache the result
    cache[description_hash] = ipfs_hash
    try:
        with open(cache_file, 'w') as f:
            json.dump(cache, f, indent=2)
        logger.info(f"Cached IPFS hash for future use")
    except IOError as e:
        logger.warning(f"Could not save IPFS cache: {e}")

    return f"ipfs:{ipfs_hash}"


def _prepare_evm_script(dao: DAOParameters, actions):
    aragon_agent = abi.aragon_agent.at(dao.agent)

    evm_script = bytes.fromhex("00000001")

    for action in actions:
        agent_calldata = aragon_agent.execute.prepare_calldata(
            action.address, 0, action.calldata
        )

        length = bytes.fromhex(hex(len(agent_calldata.hex()) // 2)[2:].zfill(8))
        evm_script = (
            evm_script
            + bytes.fromhex(aragon_agent.address[2:])
            + length
            + agent_calldata
        )

    evm_script = HexBytes(evm_script)

    return evm_script


def _generate_preview(dao: DAOParameters, actions):
    """Generate a human-readable preview of direct and cross-chain calls."""
    preview_blocks = []
    for action in actions:
        block = f"Call via agent ({dao.agent}):\n"

        block += f" ├─ To: {action.address}\n"
        block += f" ├─ Function: {action.function_name}\n"
        outer_connector = "├" if action.xgov_calls else "└"
        block += f" {outer_connector}─ Inputs: {_format_inputs(action)}\n"

        for index, xgov_call in enumerate(action.xgov_calls):
            connector = "└" if index == len(action.xgov_calls) - 1 else "├"
            chain_name = action.xgov_chain_name or f"Chain {action.xgov_chain_id}"
            block += (
                f" {connector}─ XGov call to {xgov_call.address} "
                f"on {chain_name} ({action.xgov_chain_id})\n"
            )
            branch = "   " if connector == "└" else " │ "
            block += f" {branch}├─ Function: {xgov_call.function_name}\n"
            block += f" {branch}└─ Inputs: {_format_inputs(xgov_call)}\n"

        preview_blocks.append(block)

    # Join each action's preview block with a newline for clear separation
    print("Calldata")
    print("\n\n".join(block.strip() for block in preview_blocks))


def _create_vote(
        dao: DAOParameters, 
        actions,
        description: str,
        live_env: Optional[LiveEnv] = None,
) -> Optional[int]:
    logger.info(f"Creating vote in {'live' if live_env else 'simulation'} mode")
    
    # Prepare the EVM script
    evm_script = _prepare_evm_script(dao, actions)
    logger.info(f"EVM script prepared.")

    # For now, use empty string as placeholder

    # Get the voting contract
    voting = abi.voting.at(dao.voting)
    logger.info(f"Voting contract loaded: {voting.address}")

    # Always sim regardless of whether the vote is going live or not
    vote_id = voting.newVote(evm_script, "", False, False, sender=CONVEX_VOTER_PROXY)

    logger.info("Simulating vote creation")
    assert voting.canVote(vote_id, CONVEX_VOTER_PROXY)
    with boa.env.prank(CONVEX_VOTER_PROXY):
        voting.vote(vote_id, True, False)

    boa.env.time_travel(seconds=voting.voteTime())

    logger.info("Simulating vote execution")
    assert voting.canExecute(vote_id)
    voting.executeVote(vote_id)

    # Live voting
    if live_env:
        vote_description_hash = _pin_to_ipfs(description)
        if not live_env.set():
            return None

        # Refresh contract binding so calls use the browser environment signer
        voting = abi.voting.at(dao.voting)

        assert voting.canCreateNewVote(boa.env.eoa), "EOA cannot create new vote. Either there isn't enough veCRV balance or EOA created a vote less than 12 hours ago."

        vote_id = voting.newVote(
            evm_script,
            vote_description_hash,
            False,
            False,
            sender=boa.env.eoa,
        )
        logger.info(f"Live vote created with ID: {vote_id}")

    return vote_id


@contextmanager
def vote(
    dao: DAOParameters,
    description: str,
    live_env: Optional[LiveEnv] = None,
):
    """
    A context manager to patch boa's ABIFunction.prepare_calldata that
    generates a transaction payload.

    This context manager also behaves like a prank (where the pranked
    user is the dao agent) and like an anchor (changes are reverted
    after the `with` block).

    Inside the `with` block, any call to a mutable function on an
    ABIContract will have its calldata and decoded preview metadata captured.
    """
    # TODO forbid ops like deploying contracts inside to keep the vote clean

    captured_actions = []

    def _patched_prepare_calldata(self, *args, **kwargs):
        with use_clean_prepare_calldata():
            calldata = self.prepare_calldata(*args, **kwargs)
        if self.is_mutable:
            contract_address = str(self.contract.address)
            action = _capture_call(self, calldata)
            assert action.address == contract_address

            xgov_preview = get_xgov_preview()
            if xgov_preview is not None:
                xgov_preview.attach(action)

            captured_actions.append(action)
        return calldata

    with ExitStack() as stack:
        def _cleanup():
            print(f"Metadata\n{description}\n")
            _generate_preview(dao, captured_actions)
            _create_vote(dao, captured_actions, description, live_env)

        stack.callback(_cleanup)

        stack.enter_context(boa.env.prank(dao.agent)) 
        stack.enter_context(boa.env.anchor())
        stack.enter_context(use_dao(dao))
        stack.enter_context(use_prepare_calldata(_patched_prepare_calldata))

        yield


@contextmanager
def xvote(
    chain: Chain,
    rpc: str,
    broadcaster_parameters: Optional[dict]=None,
):
    """
    Works similarly to `vote` and is intended to be used inside a vote context:

    ```py
    from voting.xgov.chains import FRAXTAL

    with vote(OWNERSHIP, description="[Frax] Set things."):
        with xvote(FRAXTAL, "https://rpc.frax.com"):
            things.set()
    ```
    """

    messages = []
    captured_calls = []

    def _patched_prepare_calldata(self, *args, **kwargs):
        with use_clean_prepare_calldata():
            calldata = self.prepare_calldata(*args, **kwargs)
        if self.is_mutable:
            contract_address = str(self.contract.address)
            messages.append((contract_address, calldata))
            captured_calls.append(_capture_call(self, calldata))
        return calldata  # calldata is prepared, but I need gas_used available after execution

    fork_params = {"url": rpc, "allow_dirty": True}

    dao_params = get_dao()

    with ExitStack() as stack:
        stack.enter_context(boa.env.anchor())
        stack.enter_context(boa.fork(**fork_params))

        stack.enter_context(boa.env.prank(chain.agent_address(dao_params)))
        stack.enter_context(use_prepare_calldata(_patched_prepare_calldata))

        yield
    xgov_preview = _XGovPreview(chain, captured_calls)
    with use_xgov_preview(xgov_preview):
        chain.broadcast(dao_params, fork_params, messages, broadcaster_parameters)
    xgov_preview.assert_complete()


@contextmanager
def vote_test():
    """
    Context manager to do tests inside a vote context, so they aren't taken into actions.

    ```py
    with vote(OWNERSHIP, description="Set things."):
        things.set()
        with vote_test():
            things.do_something()
            assert things.went_as_set()
    """
    with use_clean_prepare_calldata():
        yield
