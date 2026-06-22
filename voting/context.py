from contextlib import contextmanager

from boa.contracts.abi.abi_contract import ABIFunction

from voting.config import DAOParameters

_dao = None
_xgov_preview = None
_clean_prepare_calldata = ABIFunction.prepare_calldata


@contextmanager
def use_dao(dao: DAOParameters):
    global _dao
    assert not _dao, "DAO is already set"
    _dao = dao
    try:
        yield
    finally:
        _dao = None


def get_dao() -> DAOParameters:
    assert _dao, "No DAO set"
    return _dao


@contextmanager
def use_xgov_preview(preview):
    global _xgov_preview
    assert _xgov_preview is None, "XGov preview is already set"
    _xgov_preview = preview
    try:
        yield
    finally:
        _xgov_preview = None


def get_xgov_preview():
    return _xgov_preview


@contextmanager
def use_prepare_calldata(_prepare_calldata):
    prev_prepare_calldata = ABIFunction.prepare_calldata
    ABIFunction.prepare_calldata = _prepare_calldata
    try:
        yield
    finally:
        ABIFunction.prepare_calldata = prev_prepare_calldata


@contextmanager
def use_clean_prepare_calldata():
    with use_prepare_calldata(_clean_prepare_calldata):
        yield
