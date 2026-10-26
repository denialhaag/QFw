# The Garnet mock backend name selects the shim QPM, like `pasqal` does.

import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
for path in (REPO_ROOT / "backends", REPO_ROOT / "services"):
	if str(path) not in sys.path:
		sys.path.insert(0, str(path))


def test_garnet_mock_name_selects_the_shim_provider():
	from qfw_qiskit.qpm_selection import qpm_selection_for_provider
	selection = qpm_selection_for_provider("iqm-garnet-mock")

	assert selection == qpm_selection_for_provider("shim")
	assert selection["provider"] == "shim"
