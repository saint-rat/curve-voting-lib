import json

import boa
import pytest

from voting import OWNERSHIP, abi
from voting.create_vote import (
    _XGovPreview,
    _capture_call,
    _format_value,
    _generate_preview,
)
from voting.xgov.chains import Chain, OPTIMISM


CONFIGURATOR_ADDRESS = "0xd36c590531cAF5F620C57Faf5827Ce8E7f6E5Bec"

CONFIGURATOR_ABI = boa.loads_abi(
    name="PreviewConfigurator",
    json_str=json.dumps(
        [
            {
                "type": "function",
                "name": "set_admin_percentage",
                "stateMutability": "nonpayable",
                "inputs": [
                    {"name": "_controller", "type": "address"},
                    {"name": "_admin_percentage", "type": "uint256"},
                ],
                "outputs": [],
            }
        ]
    ),
)

DIRECT_ABI = boa.loads_abi(
    name="PreviewDirectCall",
    json_str=json.dumps(
        [
            {
                "type": "function",
                "name": "set_value",
                "stateMutability": "nonpayable",
                "inputs": [{"name": "value", "type": "uint256"}],
                "outputs": [],
            }
        ]
    ),
)


@pytest.fixture(autouse=True)
def fork_chain():
    """Override the integration-test fork fixture for these pure unit tests."""
    yield


def _capture_configurator_call(controller: str, value: int):
    configurator = CONFIGURATOR_ABI.at(CONFIGURATOR_ADDRESS, nowarn=True)
    calldata = configurator.set_admin_percentage.prepare_calldata(controller, value)
    return _capture_call(configurator.set_admin_percentage, calldata)


def _capture_broadcast(messages, gas_limit=200_000):
    broadcaster = abi.broadcasters["optimism"].at(
        "0x8e1e5001C7B8920196c7E3EdF2BCf47B2B6153ff", nowarn=True
    )
    calldata = broadcaster.broadcast.prepare_calldata(messages, gas_limit)
    function = broadcaster.method_id_map[calldata[:4]]
    return _capture_call(function, calldata)


def test_format_value_recurses_into_tuples_and_lists():
    value = ((b"\x9f\x2b\x79\xd6", [b"\x00\xff"]),)

    assert _format_value(value) == (("9f2b79d6", ["00ff"]),)


def test_xgov_preview_decodes_nested_calls(capsys):
    controllers = [f"0x{index:040x}" for index in range(1, 4)]
    calls = [
        _capture_configurator_call(controller, 10**17)
        for controller in controllers
    ]
    messages = [(call.address, call.calldata) for call in calls]
    broadcaster_call = _capture_broadcast(messages, 224_317)
    preview = _XGovPreview(OPTIMISM, calls)
    preview.attach(broadcaster_call)
    preview.assert_complete()

    _generate_preview(OWNERSHIP, [broadcaster_call])
    output = capsys.readouterr().out

    assert "b'\\x" not in output
    assert "Address(" not in output
    assert "9f2b79d6" in output
    assert output.count("XGov call to") == 3
    assert output.count("Function: set_admin_percentage") == 3
    assert "on Optimism (10)" in output
    assert "('uint256', '_admin_percentage', '100000000000000000')" in output


def test_xgov_preview_associates_calls_across_chunks():
    calls = [
        _capture_configurator_call(f"0x{index:040x}", 10**17 + index)
        for index in range(1, 10)
    ]
    messages = [(call.address, call.calldata) for call in calls]
    first_broadcast = _capture_broadcast(messages[:8])
    second_broadcast = _capture_broadcast(messages[8:])
    preview = _XGovPreview(OPTIMISM, calls)

    preview.attach(first_broadcast)
    preview.attach(second_broadcast)
    preview.assert_complete()

    assert first_broadcast.xgov_calls == calls[:8]
    assert second_broadcast.xgov_calls == calls[8:]


def test_custom_chain_without_name_uses_id_fallback():
    custom_chain = Chain(
        999_999,
        "https://example.invalid",
        OPTIMISM.broadcaster,
        "0x0000000000000000000000000000000000000001",
    )
    call = _capture_configurator_call(
        "0x0000000000000000000000000000000000000002", 10**17
    )
    broadcaster_call = _capture_broadcast([(call.address, call.calldata)])
    preview = _XGovPreview(custom_chain, [call])

    preview.attach(broadcaster_call)

    assert broadcaster_call.xgov_chain_name == "Chain 999999"


def test_direct_call_preview_is_unchanged(capsys):
    contract = DIRECT_ABI.at(
        "0x000000000000000000000000000000000000002A", nowarn=True
    )
    calldata = contract.set_value.prepare_calldata(42)
    call = _capture_call(contract.set_value, calldata)

    _generate_preview(OWNERSHIP, [call])
    output = capsys.readouterr().out

    assert "Function: set_value" in output
    assert "('uint256', 'value', '42')" in output
    assert "XGov call" not in output


def test_undecodable_calldata_falls_back_to_hex():
    contract = DIRECT_ABI.at(
        "0x000000000000000000000000000000000000002A", nowarn=True
    )

    call = _capture_call(contract.set_value, b"\x12\x34")

    assert call.inputs[0].type == "bytes"
    assert call.inputs[0].name == "calldata"
    assert call.inputs[0].value == "1234"

