import os
from pathlib import Path

import boa

from voting import PARAMETER, abi, vote


# NEW PARAMETERS TO SET
## Commit apply parameters
NEW_MID_FEE = 330_000
NEW_OUT_FEE = 726_300_000
NEW_FEE_GAMMA = 116_255_000_000_000_000
NEW_ALLOWED_EXTRA_PROFIT = 440_000_000_000
NEW_ADJUSTMENT_STEP = 24_500_000_000_000
NEW_MA_HALF_TIME = 600
NEW_ADMIN_FEE = 5_000_000_000

## ramping parameters
NEW_A = 17_076_290
NEW_GAMMA = 27_042_994_328_403
RAMP_DAYS = 14


# CURRENT PARAMETERS FOR VALIDATION
# Expected state before the vote
CURRENT_MID_FEE = 3_000_000
CURRENT_OUT_FEE = 30_000_000
CURRENT_FEE_GAMMA = 500_000_000_000_000
CURRENT_ALLOWED_EXTRA_PROFIT = 2_000_000_000_000
CURRENT_ADJUSTMENT_STEP = 490_000_000_000_000
CURRENT_MA_HALF_TIME = 600
CURRENT_ADMIN_FEE = 5_000_000_000

CURRENT_A = 1_707_629
CURRENT_GAMMA = 11_809_167_828_997


# Vote configuration
POOL_ADDRESS = "0xd51a44d3fae010294c616388b506acda1bfaae46"
PROXY_ADDRESS = "0x7a1f2f99b65f6c3b2413648c86c0326cff8d8837"
CONTRACTS_DIR = Path(__file__).resolve().parents[2] / "contracts"

# Constants
DAY = 86_400 # seconds in a day

def load_contract(name: str, address: str):
    abi_path = CONTRACTS_DIR / address.lower() / "abi.json"
    return boa.loads_abi(name=name, json_str=abi_path.read_text()).at(address)

def same_address(left, right) -> bool:
    return str(left).lower() == str(right).lower()


assert RAMP_DAYS >= 1, "RAMP_DAYS must be at least one day"

rpc_url = os.getenv("RPC_URL")
if not rpc_url:
    raise ValueError("RPC_URL environment variable is required")

boa.fork(rpc_url)

pool = load_contract("Tricrypto pool", POOL_ADDRESS)
proxy = load_contract("Curve Crypto Pool Proxy", PROXY_ADDRESS)
parameter_voting = abi.voting.at(PARAMETER.voting)

current_timestamp = boa.env.evm.patch.timestamp
parameter_vote_time = parameter_voting.voteTime()
future_time = current_timestamp + parameter_vote_time + RAMP_DAYS * DAY

# Validate the ownership and authorization path.
assert same_address(pool.owner(), PROXY_ADDRESS)
assert same_address(proxy.parameter_admin(), PARAMETER.agent)

# Refuse to construct the vote from an unexpected starting state.
assert pool.A() == CURRENT_A
assert pool.gamma() == CURRENT_GAMMA
assert pool.mid_fee() == CURRENT_MID_FEE
assert pool.out_fee() == CURRENT_OUT_FEE
assert pool.fee_gamma() == CURRENT_FEE_GAMMA
assert pool.allowed_extra_profit() == CURRENT_ALLOWED_EXTRA_PROFIT
assert pool.adjustment_step() == CURRENT_ADJUSTMENT_STEP
assert pool.ma_half_time() == CURRENT_MA_HALF_TIME
assert pool.admin_fee() == CURRENT_ADMIN_FEE
assert pool.admin_actions_deadline() == 0
assert pool.future_A_gamma_time() <= current_timestamp

description = (
    f"Optimize pool parameters for Tricrypto2 ({POOL_ADDRESS}) and ramp "
    f"A and gamma over {RAMP_DAYS} days."
)

with vote(PARAMETER, description):
    proxy.commit_new_parameters(
        POOL_ADDRESS,
        NEW_MID_FEE,
        NEW_OUT_FEE,
        NEW_ADMIN_FEE,
        NEW_FEE_GAMMA,
        NEW_ALLOWED_EXTRA_PROFIT,
        NEW_ADJUSTMENT_STEP,
        NEW_MA_HALF_TIME,
    )

    proxy.ramp_A_gamma(
        POOL_ADDRESS,
        NEW_A,
        NEW_GAMMA,
        future_time,
    )

    # The commit stages values but does not apply them.
    assert pool.future_mid_fee() == NEW_MID_FEE
    assert pool.future_out_fee() == NEW_OUT_FEE
    assert pool.future_admin_fee() == NEW_ADMIN_FEE
    assert pool.future_fee_gamma() == NEW_FEE_GAMMA
    assert pool.future_allowed_extra_profit() == NEW_ALLOWED_EXTRA_PROFIT
    assert pool.future_adjustment_step() == NEW_ADJUSTMENT_STEP
    assert pool.future_ma_half_time() == NEW_MA_HALF_TIME

    assert pool.mid_fee() == CURRENT_MID_FEE
    assert pool.out_fee() == CURRENT_OUT_FEE
    assert pool.fee_gamma() == CURRENT_FEE_GAMMA
    assert pool.allowed_extra_profit() == CURRENT_ALLOWED_EXTRA_PROFIT
    assert pool.adjustment_step() == CURRENT_ADJUSTMENT_STEP
    assert pool.ma_half_time() == CURRENT_MA_HALF_TIME
    assert pool.admin_fee() == CURRENT_ADMIN_FEE

    packed_A_gamma = pool.future_A_gamma()
    assert packed_A_gamma >> 128 == NEW_A
    assert packed_A_gamma & (2**128 - 1) == NEW_GAMMA
    assert pool.future_A_gamma_time() == future_time