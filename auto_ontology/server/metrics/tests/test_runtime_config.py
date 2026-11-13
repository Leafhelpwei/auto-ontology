# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""The default runtime is available, while explicit rollback remains reliable."""

import pytest

from auto_ontology.semantic.contracts.models import ContractError
from auto_ontology.server.metrics import service


def test_unconfigured_runtime_is_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUTO_ONTOLOGY_GOVERNED_METRICS_ENABLED", raising=False)
    assert service.enabled() is True
    service.require_enabled()


@pytest.mark.parametrize("value", ["true", "TRUE", " true ", "1", "yes"])
def test_explicit_enabled_values(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("AUTO_ONTOLOGY_GOVERNED_METRICS_ENABLED", value)
    assert service.enabled() is True
    service.require_enabled()


@pytest.mark.parametrize(
    "value", ["false", "FALSE", " false ", "0", "no", "", "invalid"]
)
def test_explicit_disabled_values_block_runtime(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("AUTO_ONTOLOGY_GOVERNED_METRICS_ENABLED", value)
    assert service.enabled() is False
    with pytest.raises(ContractError) as error:
        service.require_enabled()
    assert error.value.code == "FEATURE_DISABLED"
