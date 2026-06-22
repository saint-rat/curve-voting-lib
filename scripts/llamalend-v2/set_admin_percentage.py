import json
import os

import boa

from voting import BrowserEnv, OWNERSHIP, vote, xvote
from voting.xgov.chains import OPTIMISM


CONFIGURATOR_ADDRESS = "0xd36c590531cAF5F620C57Faf5827Ce8E7f6E5Bec"
CONTROLLERS = {
    "wstETH/WETH": "0x745422BF49f3F6e4A8E12E4abD19339E7910F8C9",
    "WBTC/USDC": "0x9fC15ac3EF97093832f49B7997A58E29b49C56dE",
    "wstETH/USDC": "0xb5EC7A3D591877A66BE4f3eafdC4205E98A1BCAA",
}

WAD = 10**18
CURRENT_ADMIN_PERCENTAGE = WAD // 100  # 1%
NEW_ADMIN_PERCENTAGE = WAD // 10  # 10%

CONFIGURATOR_ABI = boa.loads_abi(
    name="LlamaLendV2Configurator",
    json_str=json.dumps(
        [
            {
                "type": "function",
                "name": "default_admin",
                "stateMutability": "view",
                "inputs": [],
                "outputs": [{"name": "", "type": "address"}],
            },
            {
                "type": "function",
                "name": "set_admin_percentage",
                "stateMutability": "nonpayable",
                "inputs": [
                    {"name": "_controller", "type": "address"},
                    {"name": "_admin_percentage", "type": "uint256"},
                ],
                "outputs": [],
            },
            {   
                "inputs": [
                    {"name":"_controller","type":"address"},{"name":"_borrow_cap","type":"uint256"}
                ],
                "name":"set_borrow_cap",
                "outputs":[],
                "stateMutability":"nonpayable",
                "type":"function"
            }
        ]
    ),
)

CONTROLLER_ABI = boa.loads_abi(
    name="LlamaLendV2Controller",
    json_str=json.dumps(
        [
            {
                "type": "function",
                "name": "admin_percentage",
                "stateMutability": "view",
                "inputs": [],
                "outputs": [{"name": "", "type": "uint256"}],
            },
            {
                "type": "function",
                "name": "configurator",
                "stateMutability": "view",
                "inputs": [],
                "outputs": [{"name": "", "type": "address"}],
            },
        ]
    ),
)


def main():
    mainnet_rpc = os.environ.get("RPC_URL")
    if not mainnet_rpc:
        raise ValueError("RPC_URL must be set to an Ethereum mainnet RPC URL")

    optimism_rpc = os.environ.get("OPTIMISM_RPC_URL", OPTIMISM.rpc)
    live = os.environ.get("LIVE", "0") == "1"
    rationale_url = os.environ.get("RATIONALE_URL")

    if live and not rationale_url:
        raise ValueError("RATIONALE_URL must be set when LIVE=1")

    description = (
        "[Optimism] Increase the admin fee percentage from 1% to 10% on three "
        f"LlamaLend v2 markets via the Configurator ({CONFIGURATOR_ADDRESS}): "
        + "; ".join(
            f"{name} ({address})" for name, address in CONTROLLERS.items()
        )
        + "."
    )
    if rationale_url:
        description += f" Rationale: {rationale_url}"

    boa.fork(mainnet_rpc)

    with vote(
        OWNERSHIP,
        description,
        live_env=BrowserEnv() if live else None,
    ):
        with xvote(OPTIMISM, optimism_rpc):
            configurator = CONFIGURATOR_ABI.at(CONFIGURATOR_ADDRESS)

            assert configurator.default_admin() == OPTIMISM.agent_address(OWNERSHIP)

            for name, address in CONTROLLERS.items():
                controller = CONTROLLER_ABI.at(address)
                assert controller.configurator() == CONFIGURATOR_ADDRESS, (
                    f"{name}: unexpected configurator"
                )
                assert controller.admin_percentage() == CURRENT_ADMIN_PERCENTAGE, (
                    f"{name}: expected current admin percentage of 1%"
                )

                configurator.set_admin_percentage(address, NEW_ADMIN_PERCENTAGE)

                assert controller.admin_percentage() == NEW_ADMIN_PERCENTAGE, (
                    f"{name}: admin percentage was not updated to 10%"
                )

if __name__ == "__main__":
    main()