#!/usr/bin/env python3
"""Local controller qualification. No Azure operations or existing credentials."""

import base64
import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tarfile
import unittest
from unittest.mock import Mock, patch
import uuid
import time


RECIPE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("dracut_deploy", RECIPE / "scripts/deploy.py")
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)
upload_spec = importlib.util.spec_from_file_location("dracut_upload", RECIPE / "scripts/deployment-upload.py")
uploader = importlib.util.module_from_spec(upload_spec)
upload_spec.loader.exec_module(uploader)


class Fixture(unittest.TestCase):
    def setUp(self):
        # All scratch data stays inside the checkout, never a system temp dir.
        self.workspace = Path.cwd() / (".deployment-test-" + uuid.uuid4().hex)
        self.workspace.mkdir(mode=0o700)
        self.addCleanup(shutil.rmtree, self.workspace)
        self.checkout_patch = patch.object(deploy, "CHECKOUT", self.workspace / "checkout")
        self.checkout_patch.start()
        self.addCleanup(self.checkout_patch.stop)
        self.arguments = [
            "deploy", "--subscription-id", "00000000-0000-0000-0000-000000000001",
            "--resource-group", "new-dracut-group", "--prefix", "drtest",
            "--ssh-public-key", str(self.workspace / "nonexistent key.pub"),
            "--run-directory", str(self.workspace / "runs" / "new"),
            "--maa-endpoint", "https://approved.attest.azure.net",
            "--ssh-source-cidr", "10.80.1.0/24",
        ]
        self.config = deploy.configuration(deploy.parser().parse_args(self.arguments))
        self.run = Path(self.config["runDirectory"])
        self.run.mkdir(parents=True, mode=0o700)
        (self.run / "evidence").mkdir(mode=0o700)
        self.engine = deploy.Engine(self.config, str(self.workspace / "fake key"))
        self.boot1 = "10000000-0000-0000-0000-000000000001"
        self.boot2 = "20000000-0000-0000-0000-000000000002"
        self.build = {"rootHash": "ab" * 32, "attestationSdk": {
            "revision": "unit-test-revision", "binarySha256": "cd" * 32, "wrapperSha256": "ef" * 32}}

    def model(self, role="reference"):
        return {
            "id": deploy.base_id(self.config) + "/providers/Microsoft.Compute/virtualMachines/" + self.config[role + "Vm"],
            "name": self.config[role + "Vm"],
            "tags": {"purpose": deploy.PURPOSE, "runId": self.config["runId"], "role": role},
        }

    def acceptance(self, reboot=False):
        return {
            "result": "GUEST_ACCEPTANCE_PASSED", "rootHash": self.build["rootHash"],
            "attestationSdk": self.build["attestationSdk"],
            "bootId": self.boot2 if reboot else self.boot1,
            "cloudInit": {"status": "done", "errors": [], "recoverable_errors": {}},
            "workload": "absent after distinct boot" if reboot else {
                "allocatedBytes": 128 * 1024 * 1024, "allocatedInodes": 2050},
        }

    def receipt(self):
        return {
            "result": "EVIDENCE_COLLECTED",
            "verification": "NOT_VERIFIED: verify MAA token and authenticated quote remotely",
            "nonceHex": "aa" * 32, "requestedPcrs": deploy.PCRS, "reportedPcr11Sha256": "0c" * 32,
            "sdkBuild": {k: v for k, v in self.build["attestationSdk"].items() if k != "wrapperSha256"},
            "sdkPathUntrusted": "/usr/local/bin/azure-guest-attest",
            "tokenClaimsUnverified": {"reportedPcrs": deploy.PCRS[:-1], "missingRequestedPcrs": [12],
                                      "requestedCoverageComplete": False,
                                      "reportedPcrValuesSha256": {
                                          str(index): bytes([index + 1]).hex() * 32 for index in deploy.PCRS[:-1]}},
        }

    def attestation_files(self, nonce):
        def b64(data):
            return base64.b64encode(data).decode()
        request = {
            "TpmInfo": {"PcrSet": deploy.PCRS, "PCRs": [
                {"Index": index, "Digest": b64(bytes([index + 1]) * 32)} for index in deploy.PCRS],
                **{field: b64(b"unverified unit fixture") for field in ("PcrQuote", "PcrSignature", "AikCert", "AikPub")}},
            "TcgLogs": b64(b"unverified event log"), "IsolationInfo": {"Type": "Tdx", "Evidence": {
                "Proof": b64(b"unverified TDX proof"), "RunTimeData": b64(b"unit runtime")}},
        }
        claims = {"x-ms-azurevm-attested-pcrs": deploy.PCRS[:-1],
                  "x-ms-azurevm-attested-pcr-values": {
                      f"pcr{index}": b64(bytes([index + 1]) * 32) for index in deploy.PCRS[:-1]}}
        token = b64(b'{"alg":"RS256"}') + "." + b64(json.dumps(claims).encode()) + "." + b64(b"not-a-real-signature")
        response = {"provider": "maa", "endpoint": self.config["maaEndpoint"], "passed": True,
                    "token": token, "request": json.dumps(request)}
        receipt = dict(self.receipt(), nonceHex=nonce)
        return {
            "sdk-stderr.log": b"", "sdk-response.json": json.dumps(response).encode(),
            "request.json": json.dumps(request).encode(), "receipt.json": json.dumps(receipt).encode(),
            "token.txt": (token + "\n").encode(),
        }


class PlanningTests(Fixture):
    def test_plan_needs_no_key_private_key_credentials_tools_or_writes(self):
        self.arguments[self.arguments.index("--run-directory") + 1] = str(self.workspace / "not-created")
        for flag in ("--maa-endpoint", "--ssh-source-cidr"):
            index = self.arguments.index(flag)
            del self.arguments[index:index + 2]
        with patch.object(deploy.subprocess, "run") as process, \
                patch.object(deploy, "private_write") as write, \
                patch.object(deploy.shutil, "which", side_effect=AssertionError("tools checked")), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(deploy.main(self.arguments + ["--plan"]), 0)
        process.assert_not_called()
        write.assert_not_called()
        config = json.loads(output.getvalue())
        self.assertEqual(config["vmSize"], "Standard_DC4es_v6")
        self.assertEqual(config["builderImage"], "Canonical:ubuntu-24_04-lts:server:24.04.202610250")
        self.assertEqual(config["sourceImage"], "Canonical:ubuntu-24_04-lts:cvm:24.04.202610280")
        self.assertFalse(Path(config["runDirectory"]).exists())
        self.assertEqual(config["maximumVms"], 2)

    def test_rejects_overwrites_and_paths_inside_checkout_even_for_plan(self):
        with self.assertRaisesRegex(deploy.DeploymentError, "new run directory"):
            deploy.configuration(deploy.parser().parse_args(self.arguments + ["--plan"]))
        new = self.workspace / "checkout" / "inside"
        self.arguments[self.arguments.index("--run-directory") + 1] = str(new)
        with self.assertRaisesRegex(deploy.DeploymentError, "outside"):
            deploy.configuration(deploy.parser().parse_args(self.arguments + ["--plan"]))

    def test_rejects_public_ssh_wrong_tee_unpinned_builder_and_bad_endpoint(self):
        for option, value in (
            ("--ssh-source-cidr", "0.0.0.0/0"), ("--ssh-source-cidr", "8.8.8.8/32"),
            ("--vm-size", "Standard_DC4as_v6"), ("--builder-image", "Canonical:ubuntu-24_04-lts:server:latest"),
            ("--maa-endpoint", "http://approved.attest.azure.net"),
            ("--maa-endpoint", "https://user:secret@approved.attest.azure.net"),
        ):
            args = self.arguments[:]
            args[args.index("--run-directory") + 1] = str(self.workspace / "new-other")
            args += [option, value]
            with self.subTest(option=option, value=value), self.assertRaises(deploy.DeploymentError):
                deploy.configuration(deploy.parser().parse_args(args))

    def test_execution_requires_maa_private_key_and_private_path_before_azure(self):
        args = self.arguments[:]
        args[args.index("--run-directory") + 1] = str(self.workspace / "execution-new")
        with patch.object(deploy.subprocess, "run") as process, \
                self.assertRaisesRegex(deploy.DeploymentError, "ssh-private-key"):
            deploy.main(args)
        process.assert_not_called()
        self.assertFalse((self.workspace / "execution-new").exists())

    def test_private_artifact_mode_and_no_overwrite(self):
        target = self.run / "private.json"
        deploy.private_write(target, "token-shaped-test-data")
        if os.name != "nt":
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
        with self.assertRaises(FileExistsError):
            deploy.private_write(target, "overwrite")
        self.assertEqual(target.read_text(), "token-shaped-test-data")


class AzureBoundaryTests(Fixture):
    def test_windows_azure_cli_bypasses_batch_reparsing(self):
        with patch.object(deploy.os, "name", "nt"), patch.object(deploy, "Path", type(self.workspace)), \
                patch.object(deploy.shutil, "which", return_value="C:/Azure/CLI2/wbin/az.cmd"), \
                patch.object(type(self.workspace), "is_file", return_value=True):
            executable = deploy.azure_executable()
        self.assertEqual(executable, [str(Path("C:/Azure/CLI2/python.exe")), "-IBm", "azure.cli"])

    def test_windows_rest_json_preserves_quotes_spaces_percent_and_ampersands_without_cmd(self):
        body = {"properties": {"source": {"script": 'printf "%s" "path with spaces & 100% literal"'}}}
        with patch.object(deploy.os, "name", "nt"), patch.object(deploy, "Path", type(self.workspace)), \
                patch.object(deploy.shutil, "which", return_value="C:/Azure CLI/CLI2/wbin/az.cmd"), \
                patch.object(type(self.workspace), "is_file", return_value=True), \
                patch.object(deploy.subprocess, "run",
                             return_value=subprocess.CompletedProcess([], 0, b"{}", b"")) as process:
            self.engine.rest("put", deploy.base_id(self.config) + "/test/resource", body,
                             expand="ReplicationStatus,UefiSettings")
        args = process.call_args.args[0]
        self.assertEqual(args[:4], [str(Path("C:/Azure CLI/CLI2/python.exe")), "-IBm", "azure.cli", "rest"])
        self.assertEqual(json.loads(args[args.index("--body") + 1]), body)
        self.assertEqual(args[args.index("--url-parameters") + 1], "$expand=ReplicationStatus,UefiSettings")
        self.assertFalse(any(arg.lower().endswith((".cmd", ".bat")) for arg in args))
        self.assertFalse(process.call_args.kwargs.get("shell", False))

    def test_rest_uses_inline_json_and_explicit_subscription_without_credential_flags(self):
        body = {"properties": {"source": {"script": "echo 'path with spaces' \"quoted\""}}}
        with patch.object(deploy.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, b"{}", b"")) as run:
            self.engine.rest("put", deploy.base_id(self.config) + "/a/b", body)
        args = run.call_args.args[0]
        self.assertEqual(json.loads(args[args.index("--body") + 1]), body)
        self.assertFalse(args[args.index("--body") + 1].startswith("@"))
        self.assertEqual(args[args.index("--subscription") + 1], self.config["subscriptionId"])
        self.assertNotIn("account", args)
        self.assertNotIn("--sas-token", args)
        self.assertNotIn("--account-key", args)

    def test_command_error_does_not_log_credentials_or_raw_azure_errors(self):
        with patch.object(deploy.subprocess, "run", return_value=subprocess.CompletedProcess(
                [], 1, b"", b"Authorization Bearer fake-token SAS=fake-secret")):
            with self.assertRaises(deploy.DeploymentError) as error:
                self.engine.az("rest", "--url", "https://management.azure.com")
        self.assertNotIn("fake-token", str(error.exception))
        self.assertNotIn("fake-secret", str(error.exception))

    def test_ownership_checks_group_role_and_exact_resource_boundary(self):
        self.engine.owned(self.model(), "reference")
        for change in (
            {"tags": dict(self.model()["tags"], runId="other")},
            {"tags": dict(self.model()["tags"], role="builder")},
            {"id": deploy.base_id(self.config) + "-other/providers/example/resource"},
        ):
            with self.subTest(change=change), self.assertRaises(deploy.DeploymentError):
                self.engine.owned(dict(self.model(), **change), "reference")

    def test_existing_resource_group_fails_before_resource_creation(self):
        with patch.object(self.engine, "az", return_value=True) as azure, \
                self.assertRaisesRegex(deploy.DeploymentError, "new and dedicated"):
            self.engine.preflight()
        self.assertEqual(azure.call_count, 1)

    def test_poll_rejects_terminal_managed_or_replication_failures(self):
        for value in ({"properties": {"provisioningState": "Failed"}},
                      {"properties": {"replicationStatus": {"aggregatedState": "Failed"}}}):
            with self.subTest(value=value), self.assertRaises(deploy.DeploymentError):
                self.engine.poll(lambda: value, lambda _: False, "test")

    def test_poll_timeout_is_bounded(self):
        with patch.object(deploy.time, "monotonic", side_effect=[0, 100]), \
                self.assertRaisesRegex(deploy.DeploymentError, "timed out"):
            self.engine.poll(lambda: {}, bool, "test", timeout=10)

    def test_managed_commands_are_builder_only_and_require_marker_plus_exit(self):
        for exit_code, output, state in ((1, "PASSED", "Succeeded"), (0, "missing", "Succeeded"),
                                         (0, "PASSED", "Failed")):
            response = {"properties": {"instanceView": {
                "executionState": state, "exitCode": exit_code, "output": output}}}
            with self.subTest(exit=exit_code, state=state), \
                    patch.object(self.engine, "owned_group"), patch.object(self.engine, "vm") as vm, \
                    patch.object(self.engine, "rest", side_effect=[{}, response]), \
                    self.assertRaises(deploy.DeploymentError):
                self.engine.managed("test", "echo PASSED", "PASSED")
            vm.assert_called_once_with("builder")

    def test_vm_creation_cap_prevents_third_or_duplicate_vm(self):
        for inventory in ([{"name": "one"}, {"name": "two"}],
                          [{"name": self.config["referenceVm"]}]):
            with self.subTest(inventory=inventory), patch.object(self.engine, "owned_group"), \
                    patch.object(self.engine, "az", return_value=inventory) as azure, \
                    self.assertRaisesRegex(deploy.DeploymentError, "two-VM cap"):
                self.engine.new_vm("reference", deploy.version_id(self.config))
            self.assertEqual(azure.call_count, 1)

    def test_upload_storage_readback_refuses_keys_public_blob_or_broad_network_rules(self):
        subnet = deploy.base_id(self.config) + "/providers/Microsoft.Network/virtualNetworks/v/subnets/s"
        model = {
            "allowSharedKeyAccess": False, "allowBlobPublicAccess": False, "enableHttpsTrafficOnly": True,
            "minimumTlsVersion": "TLS1_2", "networkRuleSet": {
                "defaultAction": "Deny", "bypass": "AzureServices", "ipRules": [], "resourceAccessRules": [],
                "virtualNetworkRules": [{"virtualNetworkResourceId": subnet}]},
        }
        self.engine.check_storage(model, subnet)
        for change in ({"allowSharedKeyAccess": True}, {"allowBlobPublicAccess": True},
                       {"enableHttpsTrafficOnly": False},
                       {"networkRuleSet": dict(model["networkRuleSet"], defaultAction="Allow")},
                       {"networkRuleSet": dict(model["networkRuleSet"], ipRules=[{"ipAddressOrRange": "8.8.8.8"}])}):
            with self.subTest(change=change), self.assertRaises(deploy.DeploymentError):
                self.engine.check_storage(dict(model, **change), subnet)

    def test_actual_reference_model_security_disk_and_gallery_are_checked(self):
        model = self.model()
        model.update({
            "securityProfile": {"securityType": "ConfidentialVM",
                                "uefiSettings": {"secureBootEnabled": True, "vTpmEnabled": True}},
            "hardwareProfile": {"vmSize": self.config["vmSize"]},
            "diagnosticsProfile": {"bootDiagnostics": {"enabled": True}},
            "storageProfile": {"imageReference": {"id": deploy.version_id(self.config)}, "osDisk": {
                "diskSizeGb": 64, "managedDisk": {"securityProfile": {"securityEncryptionType": "VMGuestStateOnly"}}}},
        })
        self.engine.check_reference_model(model)
        for target, key, value in (
            ("securityProfile", "securityType", "TrustedLaunch"),
            ("hardwareProfile", "vmSize", "Standard_DC4as_v6"),
        ):
            changed = copy.deepcopy(model)
            changed[target][key] = value
            with self.subTest(value=value), self.assertRaises(deploy.DeploymentError):
                self.engine.check_reference_model(changed)
        changed = copy.deepcopy(model)
        changed["storageProfile"]["osDisk"]["diskSizeGb"] = 31
        with self.assertRaises(deploy.DeploymentError):
            self.engine.check_reference_model(changed)

    def test_vm_put_has_bootdiagnostics_security_and_no_public_ip_at_creation(self):
        nic = {"NewNIC": {"id": deploy.base_id(self.config) + "/providers/Microsoft.Network/networkInterfaces/nic"}}
        key = self.workspace / "public"
        key.write_text("ssh-ed25519 fixture")
        self.config["sshPublicKey"] = str(key)
        model = self.model()
        model.update({"diagnosticsProfile": {"bootDiagnostics": {"enabled": True}},
                      "hardwareProfile": {"vmSize": self.config["vmSize"]},
                      "storageProfile": {"osDisk": {"managedDisk": {"id": "owned-disk-id"}}}})
        with patch.object(self.engine, "owned_group"), patch.object(self.engine, "az", side_effect=[[], nic, {}]), \
                patch.object(self.engine, "rest") as rest, patch.object(self.engine, "poll"), \
                patch.object(self.engine, "vm", return_value=model), patch.object(self.engine, "check_reference_model"):
            self.engine.new_vm("reference", deploy.version_id(self.config))
        body = rest.call_args.args[2]
        props = body["properties"]
        self.assertTrue(props["diagnosticsProfile"]["bootDiagnostics"]["enabled"])
        self.assertEqual(props["securityProfile"]["securityType"], "ConfidentialVM")
        self.assertEqual(props["securityProfile"]["uefiSettings"], {"secureBootEnabled": True, "vTpmEnabled": True})
        disk = props["storageProfile"]["osDisk"]
        self.assertEqual(disk["diskSizeGB"], 64)
        self.assertEqual(disk["managedDisk"]["securityProfile"]["securityEncryptionType"], "VMGuestStateOnly")
        self.assertNotIn("publicIPAddress", json.dumps(body))

    def test_failure_requests_deallocation_only_for_proven_owned_vms(self):
        self.engine.group_created = True
        owned = self.model("builder")
        unowned = dict(self.model("reference"), tags={})
        with patch.object(self.engine, "owned_group"), \
                patch.object(self.engine, "az", return_value=[owned, unowned]), \
                patch.object(self.engine, "deallocate") as deallocate, \
                contextlib.redirect_stderr(io.StringIO()):
            self.engine.failure(deploy.DeploymentError("test failure"))
        deallocate.assert_called_once_with("builder", wait=False)
        self.assertEqual(deploy.read_json(self.engine.evidence / "failure.json")["result"], "DEPLOYMENT_FAILED")
        self.assertTrue((self.engine.evidence / "reference-failure-deallocation.json").exists())


class SSHTests(Fixture):
    def serial(self, boot, key):
        return "CVM_SSH_BOOT_BEGIN " + boot + "\nssh-ed25519 " + key + " root@guest\nCVM_SSH_BOOT_END\n"

    def test_authenticated_boot_diagnostics_rotation_uses_new_known_hosts_per_boot(self):
        first_key, second_key = base64.b64encode(b"a" * 64).decode(), base64.b64encode(b"b" * 64).decode()
        logs = [self.serial(self.boot1, first_key),
                self.serial(self.boot1, first_key), self.serial(self.boot2, second_key)]
        replies = [subprocess.CompletedProcess([], 0, (self.boot1 + "\n").encode(), b""),
                   subprocess.CompletedProcess([], 0, (self.boot2 + "\n").encode(), b"")]
        with patch.object(self.engine, "vm"), patch.object(self.engine, "address", return_value="10.73.0.4"), \
                patch.object(self.engine, "az", side_effect=logs) as az, \
                patch.object(self.engine, "ssh", side_effect=replies), patch.object(deploy.time, "sleep"):
            self.assertEqual(self.engine.bootstrap("reference", "first"), self.boot1)
            first = self.engine.hosts["reference"].copy()
            self.assertEqual(self.engine.bootstrap("reference", "reboot", self.boot1), self.boot2)
        second = self.engine.hosts["reference"]
        self.assertNotEqual(first["knownHosts"], second["knownHosts"])
        self.assertNotEqual(first["hostKey"], second["hostKey"])
        self.assertIn(second_key, Path(second["knownHosts"]).read_text())
        self.assertEqual(az.call_count, 3)
        self.assertTrue(all(call.args[:3] == ("vm", "boot-diagnostics", "get-boot-log") for call in az.call_args_list))

    def test_same_host_key_after_real_reboot_is_rejected(self):
        key = base64.b64encode(b"a" * 64).decode()
        self.engine.hosts["reference"] = {"hostKey": "ssh-ed25519 " + key}
        with patch.object(self.engine, "vm"), patch.object(self.engine, "az", return_value=self.serial(self.boot2, key)), \
                self.assertRaisesRegex(deploy.DeploymentError, "regenerate"):
            self.engine.bootstrap("reference", "reboot", self.boot1)

    def test_ssh_uses_strict_per_boot_trust_no_keyscan_or_unsafe_defaults(self):
        self.engine.hosts["reference"] = {
            "knownHosts": str(self.run / "known hosts"), "alias": "test-boot", "address": "10.73.0.4"}
        with patch.object(deploy.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, b"ok", b"")) as run:
            self.engine.ssh("reference", "sudo true")
        args = run.call_args.args[0]
        self.assertIn("StrictHostKeyChecking=yes", args)
        self.assertIn("BatchMode=yes", args)
        self.assertIn("IdentitiesOnly=yes", args)
        self.assertIn("HostKeyAlias=test-boot", args)
        self.assertIn("UserKnownHostsFile=" + str(self.run / "known hosts"), args)
        self.assertNotIn("accept-new", str(args))
        self.assertNotIn("ssh-keyscan", str(args))
        self.assertNotIn("StrictHostKeyChecking=no", args)

    def test_hostkey_failure_is_terminal_never_retrusted(self):
        key = base64.b64encode(b"a" * 64).decode()
        failure = subprocess.CompletedProcess([], 255, b"", b"Host key verification failed")
        with patch.object(self.engine, "vm"), patch.object(self.engine, "address", return_value="10.73.0.4"), \
                patch.object(self.engine, "az", return_value=self.serial(self.boot1, key)), \
                patch.object(self.engine, "ssh", return_value=failure) as ssh, \
                self.assertRaisesRegex(deploy.DeploymentError, "host-key authentication failed"):
            self.engine.bootstrap("reference", "first")
        self.assertEqual(ssh.call_count, 1)

    def test_transfer_requires_independent_hash_and_never_fetches_private_signer(self):
        content = b"public build record"
        with patch.object(self.engine, "ssh", side_effect=[
                subprocess.CompletedProcess([], 0, (deploy.digest(content) + "  path\n").encode(), b""),
                subprocess.CompletedProcess([], 0, content, b"")]):
            self.assertEqual(self.engine.retrieve("builder", "/owned/build.json", self.engine.evidence / "retrieved"), content)
        with patch.object(self.engine, "ssh") as ssh, self.assertRaises(deploy.DeploymentError):
            self.engine.retrieve("builder", "/owned/signing/signer.key", self.engine.evidence / "never")
        ssh.assert_not_called()
        with patch.object(self.engine, "ssh", side_effect=[
                subprocess.CompletedProcess([], 0, b"aa" * 32 + b" path", b""),
                subprocess.CompletedProcess([], 0, b"changed", b"")]), self.assertRaisesRegex(deploy.DeploymentError, "hash mismatch"):
            self.engine.retrieve("builder", "/owned/build.json", self.engine.evidence / "bad")
        self.assertFalse((self.engine.evidence / "bad").exists())


class EvidenceTests(Fixture):
    def test_collection_error_never_becomes_success_even_with_success_shaped_receipt(self):
        files = self.attestation_files("aa" * 32)
        staged = {}
        deploy.write_json(self.engine.evidence / "build.json", self.build)
        deploy.private_write(self.engine.evidence / "signer.der", b"public fixture")

        def ssh(role, remote, data=None, **kwargs):
            output, code = b"", 0
            if remote.startswith("sudo tee "):
                staged[remote.split()[2]] = data
            elif remote.startswith("sudo sha256sum "):
                output = (deploy.digest(staged[remote.split()[2]]) + " file").encode()
            elif remote.startswith("sudo python3 "):
                output = json.dumps(self.acceptance("--previous-boot-id" in remote)).encode()
            elif remote.startswith("sudo find "):
                output = "\n".join(files).encode()
            elif remote.startswith("sudo /usr/local/bin/cvm-attest "):
                code = 1
            return subprocess.CompletedProcess([], code, output, b"")

        def retrieve(role, remote, local):
            deploy.private_write(local, files[Path(remote).name])

        with patch.object(self.engine, "bootstrap", side_effect=[self.boot1, self.boot2]), \
                patch.object(self.engine, "ssh", side_effect=ssh), \
                patch.object(self.engine, "retrieve", side_effect=retrieve), \
                patch.object(deploy.secrets, "token_hex", return_value="aa" * 32), \
                self.assertRaisesRegex(deploy.DeploymentError, "collection failed"):
            self.engine.acceptance(self.build)
        self.assertFalse((self.engine.evidence / "acceptance-complete.json").exists())
        self.assertEqual({path.name for path in (self.engine.evidence / "attestation").iterdir()}, set(files))

    def test_complete_ssh_acceptance_reboots_challenges_and_retains_all_private_files(self):
        staged = {}
        files = self.attestation_files("aa" * 32)
        commands = []

        def ssh(role, remote, data=None, check=True, **kwargs):
            self.assertEqual(role, "reference")
            commands.append(remote)
            output = b""
            if remote.startswith("sudo tee "):
                staged[remote.split()[2]] = data
            elif remote.startswith("sudo sha256sum "):
                output = (deploy.digest(staged[remote.split()[2]]) + "  staged\n").encode()
            elif remote.startswith("sudo python3 "):
                output = json.dumps(self.acceptance("--previous-boot-id" in remote)).encode()
            elif remote.startswith("sudo find "):
                output = ("\n".join(files) + "\n").encode()
            elif remote.startswith("sudo /usr/local/bin/cvm-attest "):
                self.assertIn("--nonce " + "aa" * 32, remote)
                self.assertIn("--endpoint " + self.config["maaEndpoint"], remote)
                self.assertNotIn("--sdk", remote)
            return subprocess.CompletedProcess([], 0, output, b"")

        def retrieve(role, remote, local):
            content = files[Path(remote).name]
            deploy.private_write(local, content)
            self.engine.record(Path(local).name + "-transfer", {"sha256": deploy.digest(content)})
            return content

        deploy.write_json(self.engine.evidence / "build.json", self.build)
        deploy.private_write(self.engine.evidence / "signer.der", b"independent public DER fixture")
        with patch.object(self.engine, "bootstrap", side_effect=[self.boot1, self.boot2]) as bootstrap, \
                patch.object(self.engine, "ssh", side_effect=ssh), \
                patch.object(self.engine, "retrieve", side_effect=retrieve), \
                patch.object(deploy.secrets, "token_hex", return_value="aa" * 32):
            self.engine.acceptance(self.build)
        self.assertIn("sudo systemd-run --unit=cvm-controller-reboot", "\n".join(commands))
        self.assertEqual(bootstrap.call_args_list[1].kwargs["previous"], self.boot1)
        summary = deploy.read_json(self.engine.evidence / "acceptance-complete.json")
        self.assertEqual(summary["result"], "ACCEPTANCE_COMPLETE")
        self.assertEqual(summary["tokenMissingRequestedPcrs"], [12])
        self.assertTrue(summary["attestationVerification"].startswith("NOT_VERIFIED"))
        for name in files:
            path = self.engine.evidence / "attestation" / name
            self.assertTrue(path.is_file())
            if os.name != "nt":
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_attestation_file_gate_rejects_changed_tokens_or_wrong_tee(self):
        local = self.engine.evidence / "attestation"
        local.mkdir(mode=0o700)
        files = self.attestation_files("aa" * 32)
        for name, content in files.items():
            deploy.private_write(local / name, content)
            self.engine.record(name + "-transfer", {"sha256": deploy.digest(content)})
        self.engine.validate_attestation_files(local, "aa" * 32, self.build)
        deploy.private_write(local / "token.txt", b"changed", exclusive=False)
        with self.assertRaisesRegex(deploy.DeploymentError, "token/request"):
            self.engine.validate_attestation_files(local, "aa" * 32, self.build)
        deploy.private_write(local / "token.txt", files["token.txt"], exclusive=False)
        response = json.loads(files["sdk-response.json"])
        request = json.loads(response["request"])
        request["IsolationInfo"]["Type"] = "SevSnp"
        response["request"] = json.dumps(request)
        deploy.write_json(local / "sdk-response.json", response, exclusive=False)
        with self.assertRaisesRegex(deploy.DeploymentError, "Intel TDX"):
            self.engine.validate_attestation_files(local, "aa" * 32, self.build)

    def test_guest_thresholds_provenance_and_reboot_are_fail_closed(self):
        self.assertEqual(deploy.validate_acceptance(self.acceptance(), self.build), self.boot1)
        self.assertEqual(deploy.validate_acceptance(self.acceptance(True), self.build, self.boot1), self.boot2)
        variants = [
            {"result": "ERROR"}, {"rootHash": "bad"}, {"attestationSdk": {}},
            {"workload": {"allocatedBytes": 128 * 1024 * 1024 - 1, "allocatedInodes": 2050}},
            {"workload": {"allocatedBytes": 128 * 1024 * 1024, "allocatedInodes": 2049}},
            {"cloudInit": {"status": "running", "errors": []}},
        ]
        for change in variants:
            with self.subTest(change=change), self.assertRaises(deploy.DeploymentError):
                deploy.validate_acceptance(dict(self.acceptance(), **change), self.build)
        with self.assertRaises(deploy.DeploymentError):
            deploy.validate_acceptance(dict(self.acceptance(True), bootId=self.boot1), self.build, self.boot1)

    def test_receipt_is_not_a_verification_and_pcr12_omission_must_be_explicit(self):
        self.assertEqual(deploy.validate_receipt(self.receipt(), "aa" * 32, self.build), [12])
        for change in (
            {"result": "VERIFIED"}, {"verification": "VERIFIED"}, {"nonceHex": "bb" * 32},
            {"sdkPathUntrusted": "/custom/test-sdk"}, {"requestedPcrs": deploy.PCRS[:-1]},
            {"tokenClaimsUnverified": {"reportedPcrs": deploy.PCRS[:-1], "missingRequestedPcrs": [],
                                      "requestedCoverageComplete": True}},
        ):
            with self.subTest(change=change), self.assertRaises(deploy.DeploymentError):
                deploy.validate_receipt(dict(self.receipt(), **change), "aa" * 32, self.build)

    def gallery(self):
        self.profile = {"uefiSettings": {"signatureTemplateNames": ["MicrosoftUefiCertificateAuthorityTemplate"],
                                         "additionalSignatures": {"db": [{"type": "x509", "value": ["actual-signer"]}]}}}
        return {
            "id": deploy.version_id(self.config), "tags": {"runId": self.config["runId"]},
            "properties": {"provisioningState": "Succeeded", "replicationStatus": {"aggregatedState": "Completed"},
                           "publishingProfile": {"targetRegions": [{"name": self.config["location"]}]},
                           "securityProfile": copy.deepcopy(self.profile)},
        }

    def test_gallery_requires_exact_actual_signer_template_replication_and_identity(self):
        model = self.gallery()
        deploy.validate_gallery(model, self.profile, self.config)
        variants = []
        for path, value in (
            (["properties", "replicationStatus", "aggregatedState"], "InProgress"),
            (["properties", "securityProfile", "uefiSettings", "signatureTemplateNames"], []),
            (["properties", "securityProfile", "uefiSettings", "additionalSignatures", "db"],
             [{"type": "x509", "value": ["wrong-signer"]}]),
            (["id"], "wrong-version"), (["tags", "runId"], "wrong-run"),
        ):
            changed = copy.deepcopy(model)
            parent = changed
            for name in path[:-1]:
                parent = parent[name]
            parent[path[-1]] = value
            variants.append(changed)
        for value in variants:
            with self.subTest(value=value), self.assertRaises(deploy.DeploymentError):
                deploy.validate_gallery(value, self.profile, self.config)

    def test_real_public_profile_is_only_actual_rsa3072_der_not_another_certificate(self):
        self.assertIsNotNone(shutil.which("openssl"), "OpenSSL is required for certificate-profile tests")
        key, pem, der = (self.workspace / name for name in ("unit-signer.key", "unit-signer.pem", "unit-signer.der"))
        subprocess.run(["openssl", "req", "-new", "-x509", "-newkey", "rsa:3072", "-nodes", "-days", "1",
                        "-subj", "/CN=Local unit test/", "-keyout", str(key), "-out", str(pem)],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["openssl", "x509", "-in", str(pem), "-outform", "DER", "-out", str(der)], check=True)
        certificate = der.read_bytes()
        profile = deploy.public_profile(certificate)
        self.assertEqual(base64.b64decode(profile["uefiSettings"]["additionalSignatures"]["db"][0]["value"][0]), certificate)
        with self.assertRaises(deploy.DeploymentError):
            deploy.public_profile(certificate + b"trailing bytes")
        with self.assertRaises(deploy.DeploymentError):
            deploy.public_profile(key.read_bytes())

    def test_recipe_archive_excludes_caches_secrets_tests_and_controller(self):
        payload, manifest = deploy.recipe_archive()
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
            names = archive.getnames()
            self.assertEqual(set(names), set(manifest))
            for member in archive:
                self.assertEqual(deploy.digest(archive.extractfile(member).read()), manifest[member.name])
        self.assertIn("inputs.json", names)
        self.assertIn("patches/sdk-systemd-event-log.patch", names)
        self.assertNotIn("scripts/deploy.py", names)
        self.assertFalse(any("__pycache__" in name or name.startswith("tests/") or ".key" in name for name in names))

    def test_final_build_gate_rejects_source_kernel_signer_recipe_or_sdk_drift(self):
        _, manifest = deploy.recipe_archive()
        sdk = copy.deepcopy(deploy.read_json(RECIPE / "inputs.json")["attestationSdk"])
        sdk.update({"binarySha256": "cd" * 32, "wrapperSha256": deploy.digest((RECIPE / "attest-guest.py").read_bytes()),
                    "rustc": "rustc 1.95.0 (unit fixture)"})
        build = dict(self.build, imageUrn=self.config["sourceImage"], kernel="6.17.0-1007-azure", attestationSdk=sdk,
                     dataPartUuid=self.boot1, hashPartUuid=self.boot2)
        signer = b"public signer fixture"
        deploy.private_write(self.engine.evidence / "signer.der", signer)
        deploy.write_json(self.engine.evidence / "build.json", build)
        deploy.private_write(self.engine.evidence / "build.log", "DRACUT_VERITY_IMAGE_BUILD_PASSED\n")
        provenance = {
            "result": "DRACUT_DEPLOYMENT_BUILD_PASSED", "recipeFiles": manifest, "sourceMatchesOriginal": True,
            "sourceOriginalSha256": "ab" * 32, "sourceRawSha256": "ab" * 32, "kernel": build["kernel"],
            "sourceKernels": [{"kernel": build["kernel"], "ukiSha256": "ab" * 32}],
            "rustupBinarySha256": "ab" * 32,
            "outputs": {"image.raw": "aa" * 32, "image.vhd": "bb" * 32, "cvm.efi": "cc" * 32,
                        "signer.der": deploy.digest(signer)},
            "buildJsonSha256": deploy.digest((self.engine.evidence / "build.json").read_bytes()),
        }
        self.engine.validate_build(build, provenance, manifest)
        for change in (
            {"sourceMatchesOriginal": False}, {"sourceRawSha256": "ff" * 32}, {"kernel": "builder-host-kernel"},
            {"recipeFiles": {}}, {"outputs": dict(provenance["outputs"], **{"signer.der": "ff" * 32})},
        ):
            with self.subTest(change=change), self.assertRaises(deploy.DeploymentError):
                self.engine.validate_build(build, dict(provenance, **change), manifest)
        changed_build = copy.deepcopy(build)
        changed_build["attestationSdk"]["revision"] = "unapproved"
        with self.assertRaises(deploy.DeploymentError):
            self.engine.validate_build(changed_build, provenance, manifest)


class UploadTests(Fixture):
    def vhd(self):
        path = self.workspace / "image.vhd"
        footer = bytearray(512)
        footer[:8] = b"conectix"
        struct.pack_into(">Q", footer, 48, 1024 * 1024)
        struct.pack_into(">I", footer, 60, 2)
        struct.pack_into(">I", footer, 64, ~sum(footer) & 0xffffffff)
        path.write_bytes(bytes(1024 * 1024) + footer)
        return path

    def test_managed_identity_page_blob_upload_requires_full_independent_readback(self):
        for corrupt in (False, True):
            with self.subTest(corrupt=corrupt):
                path = self.vhd()
                content = path.read_bytes()
                remote = bytearray(len(content))
                requests = []
                properties = {
                    "x-ms-blob-type": "PageBlob", "Content-Length": str(len(content)), "ETag": "fixture",
                    "x-ms-meta-vhd_sha256": deploy.digest(content), "x-ms-meta-root_hash": self.build["rootHash"],
                }

                class Response(io.BytesIO):
                    status = 200
                    headers = properties

                def open_request(request, timeout):
                    requests.append(request)
                    if request.full_url.startswith("http://169.254.169.254/"):
                        self.assertEqual(request.headers["Metadata"], "true")
                        return Response(json.dumps({"access_token": "fake-unit-token",
                                                    "expires_on": int(time.time()) + 3600}).encode())
                    self.assertEqual(request.headers["Authorization"], "Bearer fake-unit-token")
                    if request.get_method() == "PUT" and request.full_url.endswith("?comp=page"):
                        span = request.headers["X-ms-range"].removeprefix("bytes=").split("-")
                        remote[int(span[0]):int(span[1]) + 1] = request.data
                    if request.get_method() == "GET":
                        span = request.headers["Range"].removeprefix("bytes=").split("-")
                        result = bytes(remote[int(span[0]):int(span[1]) + 1])
                        return Response((b"x" + result[1:]) if corrupt else result)
                    return Response(b"")

                target = self.engine.evidence / ("upload-bad.json" if corrupt else "upload-good.json")
                with patch.object(uploader.urllib.request, "build_opener", return_value=Mock(open=open_request)), \
                        contextlib.redirect_stdout(io.StringIO()):
                    if corrupt:
                        with self.assertRaisesRegex(RuntimeError, "SHA256 mismatch"):
                            uploader.upload(path, "https://owned.blob.core.windows.net/images/dracut.vhd",
                                            deploy.digest(content), self.build["rootHash"], target)
                        self.assertFalse(target.exists())
                    else:
                        result = uploader.upload(path, "https://owned.blob.core.windows.net/images/dracut.vhd",
                                                 deploy.digest(content), self.build["rootHash"], target)
                        self.assertEqual(result["readbackSha256"], deploy.digest(content))
                self.assertTrue(any(request.get_method() == "GET" for request in requests))
                self.assertFalse(any("sig=" in request.full_url for request in requests))

    def test_changed_vhd_or_sas_is_rejected_before_network(self):
        path = self.vhd()
        for url, expected in (("https://owned.blob.core.windows.net/images/dracut.vhd?sig=unsafe", "ab" * 32),
                              ("https://owned.blob.core.windows.net/images/dracut.vhd", "ab" * 32)):
            with self.subTest(url=url), patch.object(uploader.urllib.request, "build_opener") as network, \
                    self.assertRaises(RuntimeError):
                uploader.upload(path, url, expected, self.build["rootHash"], self.engine.evidence / "never")
            network.assert_not_called()


class CleanupTests(Fixture):
    def test_default_cleanup_refuses_incomplete_acceptance_without_deleting_or_deallocating(self):
        with patch.object(self.engine, "owned_group"), patch.object(self.engine, "az") as azure, \
                patch.object(self.engine, "deallocate") as deallocate, self.assertRaises(FileNotFoundError):
            self.engine.cleanup()
        azure.assert_not_called()
        deallocate.assert_not_called()

    def test_remove_run_requires_ownership_and_only_deletes_dedicated_group_not_local_evidence(self):
        with patch.object(self.engine, "owned_group") as owned, \
                patch.object(self.engine, "az", side_effect=[None, False]) as azure:
            self.engine.cleanup(remove=True)
        owned.assert_called_once()
        self.assertEqual(azure.call_args_list[0].args, (
            "group", "delete", "--name", self.config["resourceGroup"], "--yes", "--no-wait"))
        self.assertTrue(self.run.is_dir())
        self.assertTrue(deploy.read_json(self.engine.evidence / "cleanup-summary.json")["localEvidenceRetained"])

    def test_remove_run_cannot_bypass_failed_ownership(self):
        with patch.object(self.engine, "owned_group", side_effect=deploy.DeploymentError("not owned")), \
                patch.object(self.engine, "az") as azure, self.assertRaises(deploy.DeploymentError):
            self.engine.cleanup(remove=True)
        azure.assert_not_called()

    def test_deallocation_is_not_accepted_until_instance_view_confirms(self):
        views = [
            {"instanceView": {"statuses": [{"code": "PowerState/running"}]}},
            {"instanceView": {"statuses": [{"code": "PowerState/deallocated"}]}},
        ]
        with patch.object(self.engine, "owned_group"), patch.object(self.engine, "vm"), \
                patch.object(self.engine, "az", side_effect=[None, *views]) as azure, \
                patch.object(deploy.time, "sleep"):
            self.engine.deallocate("reference")
        self.assertEqual(azure.call_count, 3)
        self.assertEqual(deploy.read_json(self.engine.evidence / "reference-deallocation.json"), views[-1])

    def test_delete_resource_requires_actual_ownership_not_name_only(self):
        with patch.object(self.engine, "az", return_value=dict(self.model("builder"), tags={})) as azure, \
                self.assertRaises(deploy.DeploymentError):
            self.engine.delete_owned_resource(self.model("builder")["id"], "builder")
        self.assertEqual(azure.call_count, 1)


if __name__ == "__main__":
    unittest.main()
