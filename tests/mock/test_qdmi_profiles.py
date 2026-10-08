# QFw delegates session opening to the packaged MQT Core-backed adapters.
# The remaining profiles carry QFw settings, policy and legacy output handling.

import pathlib
import sys
import types

import pytest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SERVICES = str(REPO_ROOT / "services")
if SERVICES not in sys.path:
	sys.path.insert(0, SERVICES)

from defw_exception import DEFwExecutionError  # noqa: E402
from svc_lib_qpm.drivers import fomac_normalize, qdmi_driver  # noqa: E402
from svc_lib_qpm.drivers.qdmi_driver import QdmiDriver  # noqa: E402
from svc_lib_qpm.drivers.qdmi_profiles import (  # noqa: E402
	IqmQdmiProfile, profile_for)


# --- choosing a profile ------------------------------------------------------

def test_the_default_profile_is_iqm():
	assert isinstance(profile_for({}), IqmQdmiProfile)
	assert isinstance(profile_for(None), IqmQdmiProfile)
	assert isinstance(profile_for({"provider": "IQM"}), IqmQdmiProfile)


def test_an_unknown_provider_fails_when_the_driver_is_built():
	with pytest.raises(
			DEFwExecutionError, match="no profile for provider 'ionq'"):
		QdmiDriver({"provider": "ionq"})


# --- the IQM profile: the session opens as it did before ---------------------

def _module(name, **attrs):
	module = types.ModuleType(name)
	for key, value in attrs.items():
		setattr(module, key, value)
	return module


def test_the_iqm_session_uses_the_packaged_backend(monkeypatch):
	monkeypatch.setenv("QFW_QC_URL", "https://qc.example.org/")
	monkeypatch.setenv("QFW_API_KEY", "secret")
	opened = []

	def IQMBackend(**kwargs):
		opened.append(kwargs)
		return types.SimpleNamespace(device="device-handle")

	monkeypatch.setitem(sys.modules, "iqm.qdmi.qiskit", _module(
		"iqm.qdmi.qiskit", IQMBackend=IQMBackend))
	driver = QdmiDriver({
		"provider": "iqm", "id": "ornl-iqm-20q",
		"provider_device_id": "default"})

	assert driver._device() == "device-handle"
	assert opened == [{
		"base_url": "https://qc.example.org",
		"token": "secret", "qc_alias": "default"}]
	assert driver._device() == "device-handle"
	assert len(opened) == 1


def test_the_iqm_profile_names_the_missing_credential(monkeypatch):
	monkeypatch.delenv("QFW_QC_URL", raising=False)
	monkeypatch.delenv("QFW_API_KEY", raising=False)
	import util.device_access as device_access
	monkeypatch.setattr(
		device_access, "resolve_device_access",
		lambda provider=None: {
			"url": "https://qc.example.org/", "api_key": None,
			"device_id": "ornl-iqm-20q", "provider_device_id": "default"})

	with pytest.raises(DEFwExecutionError, match="API token"):
		QdmiDriver({"provider": "iqm"})._access()


def test_a_missing_device_library_names_its_package(monkeypatch):
	monkeypatch.setenv("QFW_QC_URL", "https://qc.example.org/")
	monkeypatch.setenv("QFW_API_KEY", "secret")
	# A None entry makes `import iqm` fail the way an absent package does.
	monkeypatch.setitem(sys.modules, "iqm", None)
	monkeypatch.setitem(sys.modules, "iqm.qdmi.qiskit", None)

	with pytest.raises(DEFwExecutionError, match="iqm-qdmi"):
		QdmiDriver({"provider": "iqm"})._device()


def test_the_iqm_profile_encodes_an_iqm_json_program(monkeypatch):
	import util.iqm_transcode as iqm_transcode
	calls = []

	def build_iqm_circuit(source, dynamic, mapping):
		calls.append((source, dynamic, mapping))
		return "iqm-circuit"

	monkeypatch.setattr(iqm_transcode, "build_iqm_circuit", build_iqm_circuit)
	monkeypatch.setattr(
		qdmi_driver.fomac_normalize, "extract_topology",
		lambda device: {"qubits": ["QB1", "QB2"]})
	driver = QdmiDriver({"provider": "iqm"})
	driver._serialize_program = lambda iqm_circuit: f"json:{iqm_circuit}"

	encoded = driver._profile.encode(
		driver, "OPENQASM 2.0;", {"qubit_mapping": {"q0": "QB2"}}, object())

	# The format is a name, resolved by the driver only at submission.
	assert encoded == ("json:iqm-circuit", "IQM_JSON", None)
	assert calls == [
		("OPENQASM 2.0;", {"qubits": ["QB1", "QB2"]}, {"q0": "QB2"})]


def test_the_iqm_profile_defaults():
	profile = profile_for({"provider": "iqm"})

	assert profile.calibration_set_slot == "CUSTOM1"
	assert profile.technology() is None
	assert profile.job_kwargs({}) == {}
	assert profile.check_shots(10_000) is None
	assert profile.timeout_seconds({}) == 300.0
	assert profile.timeout_seconds({"timeout": "12"}) == 12.0
	assert profile.result_counts({"01": 3}, None) == {"01": 3}


# --- run_circuit goes through the profile -----------------------------------

class _Job:
	id = "job-7"
	queue_position = 2

	def __init__(self, counts):
		self.counts = counts

	def check(self):
		return "DONE"

	def get_counts(self):
		return self.counts


def _install_program_formats(monkeypatch):
	monkeypatch.setitem(sys.modules, "mqt", _module("mqt"))
	monkeypatch.setitem(sys.modules, "mqt.core", _module("mqt.core"))
	monkeypatch.setitem(sys.modules, "mqt.core.qdmi", _module(
		"mqt.core.qdmi",
		ProgramFormat=types.SimpleNamespace(
			IQM_JSON="fmt-iqm-json", QASM3="fmt-qasm3")))


def _running_driver(monkeypatch, counts, records, submitted):
	import util.iqm_transcode as iqm_transcode
	monkeypatch.setattr(
		iqm_transcode, "build_iqm_circuit",
		lambda *args, **kwargs: "iqm-circuit")
	monkeypatch.setattr(
		qdmi_driver.fomac_normalize, "extract_topology",
		lambda device: {"qubits": ["QB1"]})

	def to_result_record(counts, shots, provider, device_id, **kwargs):
		records.append((counts, shots, provider, device_id, kwargs))
		return {"schema": "qhw-result-v1"}

	monkeypatch.setattr(
		qdmi_driver.fomac_normalize, "to_result_record", to_result_record)
	_install_program_formats(monkeypatch)

	def submit_job(program, fmt, shots, **kwargs):
		submitted.append((program, fmt, shots, kwargs))
		return _Job(counts)

	driver = QdmiDriver({"provider": "iqm", "id": "ornl-iqm-20q"})
	driver._device = lambda: types.SimpleNamespace(submit_job=submit_job)
	driver._serialize_program = lambda iqm_circuit: "{}"
	return driver


def _circuit(cid="cid-9"):
	return types.SimpleNamespace(
		info={"qasm": "OPENQASM 2.0;", "num_shots": 10, "poll_interval": 0.0},
		get_cid=lambda: cid)


def test_run_circuit_submits_what_the_profile_encodes(monkeypatch):
	records, submitted = [], []
	driver = _running_driver(monkeypatch, {"1": 10}, records, submitted)

	record = driver.run_circuit(_circuit())

	assert record == {"schema": "qhw-result-v1"}
	assert submitted == [("{}", "fmt-iqm-json", 10, {})]
	assert records == [({"1": 10}, 10, "iqm", "ornl-iqm-20q", {
		"job_id": "job-7", "status": "completed", "queue_position": 2})]
	assert driver.get_task_metadata("cid-9")["job_id"] == "job-7"


def test_run_circuit_keys_the_counts_through_the_profile(monkeypatch):
	records, submitted = [], []
	driver = _running_driver(monkeypatch, {"10": 7, "01": 3}, records, submitted)
	driver._profile.result_counts = (
		lambda counts, measurement: {k[::-1]: v for k, v in counts.items()})

	driver.run_circuit(_circuit())

	assert records[0][0] == {"01": 7, "10": 3}


def test_run_circuit_passes_the_profile_job_kwargs(monkeypatch):
	records, submitted = [], []
	driver = _running_driver(monkeypatch, {"1": 10}, records, submitted)
	driver._profile.job_kwargs = lambda info: {"custom1": "s3://bucket/run"}

	driver.run_circuit(_circuit())

	assert submitted[0][3] == {"custom1": "s3://bucket/run"}


def test_a_profile_shot_cap_stops_the_run_before_submission(monkeypatch):
	records, submitted = [], []
	driver = _running_driver(monkeypatch, {"1": 10}, records, submitted)

	def check_shots(shots):
		raise DEFwExecutionError(f"{shots} shots is over the device cap")

	driver._profile.check_shots = check_shots

	with pytest.raises(DEFwExecutionError, match="over the device cap"):
		driver.run_circuit(_circuit())

	assert submitted == []


def test_an_unknown_program_format_is_reported(monkeypatch):
	records, submitted = [], []
	driver = _running_driver(monkeypatch, {"1": 10}, records, submitted)
	driver._profile.encode = (
		lambda driver, source, info, device: ("prog", "NOT_A_FORMAT", None))

	with pytest.raises(
			DEFwExecutionError,
			match="knows no program format 'NOT_A_FORMAT'"):
		driver.run_circuit(_circuit())

	assert submitted == []


# --- fomac_normalize: the calibration slot and the technology ---------------

class _CustomDevice:
	def __init__(self, value="cal-set-1"):
		self.value = value
		self.queries = []

	def query_custom_property(self, slot, value_type):
		self.queries.append((slot, value_type))
		return self.value

	def regular_sites(self):
		return []

	def operations(self):
		return []


def _install_custom_property(monkeypatch):
	monkeypatch.setitem(sys.modules, "mqt", _module("mqt"))
	monkeypatch.setitem(sys.modules, "mqt.core", _module("mqt.core"))
	monkeypatch.setitem(sys.modules, "mqt.core.qdmi", _module(
		"mqt.core.qdmi",
		CustomProperty=types.SimpleNamespace(
			CUSTOM1="slot-1", CUSTOM2="slot-2")))


def test_the_calibration_slot_defaults_to_custom1(monkeypatch):
	_install_custom_property(monkeypatch)
	device = _CustomDevice()

	assert fomac_normalize._calibration_set_id(device) == "cal-set-1"
	assert device.queries == [("slot-1", str)]


def test_a_profile_can_point_at_another_slot(monkeypatch):
	_install_custom_property(monkeypatch)
	device = _CustomDevice()

	assert fomac_normalize._calibration_set_id(device, "CUSTOM2") == "cal-set-1"
	assert device.queries == [("slot-2", str)]


def test_no_slot_means_nothing_is_queried(monkeypatch):
	_install_custom_property(monkeypatch)
	device = _CustomDevice()

	assert fomac_normalize._calibration_set_id(device, None) is None
	cal = fomac_normalize.extract_calibration(
		device, calibration_set_slot=None)

	assert cal["calibration_set_id"] is None
	assert device.queries == []


# --- svc_qpm tags each circuit with the resource's provider -----------------

def test_the_shim_qpm_tags_circuits_with_the_provider(monkeypatch):
	from svc_lib_qpm.svc_qpm import QPM
	import util.device_access as device_access

	device = {
		"provider": "aws",
		"provider-device-id": "arn:aws:braket:::device/quantum-simulator/amazon/sv1",
		"url": "https://braket.us-east-1.amazonaws.com",
		"credential-db": "creds.json",
	}
	monkeypatch.setattr(
		device_access, "device_access_config_path", lambda: "cfg.yaml")
	monkeypatch.setattr(
		device_access, "load_yaml_config",
		lambda path: {"qpus": {"aws-sv1": device}})
	monkeypatch.setenv(device_access.QPU_DEVICE_ENV, "aws-sv1")
	# The mock tests build a QPM without running __init__, and so does this.
	qpm = QPM.__new__(QPM)

	info = qpm.prepare_circuit({"qasm": "OPENQASM 2.0;"})

	assert info["qfw_backend"] == "aws"
	# Resolved once; a later circuit does not reread the config.
	monkeypatch.setattr(
		device_access, "load_yaml_config",
		lambda path: pytest.fail("config reread"))
	assert qpm.prepare_circuit({})["qfw_backend"] == "aws"


def test_a_device_record_carries_the_profile_technology():
	pytest.importorskip("qhw_data")
	topo = {"num_qubits": 2, "qubits": ["0", "1"], "edges": [["0", "1"]],
		"operations": {}}

	with_technology = fomac_normalize.to_device_record(
		topo, "aws", "aws-sv1", technology="simulator")
	without = fomac_normalize.to_device_record(topo, "iqm", "ornl-iqm-20q")

	assert with_technology["device"]["technology"] == "simulator"
	assert "technology" not in without["device"]


def test_topology_and_calibration_share_three_qubit_loci():
	sites = [types.SimpleNamespace(name=lambda i=i: str(i)) for i in range(3)]
	queried = []

	def fidelity(*, sites):
		queried.append(tuple(site.name() for site in sites))
		return 0.9

	op = types.SimpleNamespace(
		name=lambda: "ccnot", qubits_num=lambda: 3,
		sites=lambda: sites, fidelity=fidelity)
	device = types.SimpleNamespace(operations=lambda: [op])
	assert fomac_normalize._operations(device) == {"ccnot": [["0", "1", "2"]]}
	assert fomac_normalize._operation_metrics(device) == [{
		"operation": "ccnot", "locus": ["0", "1", "2"], "fidelity": 0.9}]
	assert queried == [("0", "1", "2")]


def test_incomplete_site_tuples_are_rejected():
	op = types.SimpleNamespace(qubits_num=lambda: 3, sites=lambda: [0, 1])
	with pytest.raises(ValueError, match="incomplete"):
		fomac_normalize._operation_loci(op)
