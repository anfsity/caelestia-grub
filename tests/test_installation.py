"""Exercise real deployment commands on disposable directories, never host ESPs."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / "install.sh").read_text()


def get_function(name, closing="}"):
    opening = "(" if closing == ")" else "{"
    header = f"{name}() {opening}"
    return (
        header
        + SCRIPT.split(header, 1)[1].split(f"\n{closing}\n", 1)[0]
        + f"\n{closing}\n"
    )


class InstallationSettingsTest(unittest.TestCase):
    def read_settings(self, settings, **overrides):
        with tempfile.TemporaryDirectory(prefix="caelestia-settings-") as temporary:
            path = Path(temporary) / "install.conf"
            path.write_text(settings)
            driver = """set -euo pipefail
config=${TEST_CONFIG:-}
resolution=${TEST_RESOLUTION:-}
resolution_override=${TEST_RESOLUTION_OVERRIDE:-0}
nvram=${TEST_NVRAM:-1}
nvram_override=${TEST_NVRAM_OVERRIDE:-0}
unlock_uuids=()
root() { "$@"; }
die() { echo "$*" >&2; exit 1; }
"""
            driver += (
                get_function("add_unlock")
                + get_function("validate_resolution")
                + get_function("load_installation_settings")
            )
            driver += """load_installation_settings "$1"
validate_resolution
printf '%s\\n' "$config" "$resolution" "$nvram" "${unlock_uuids[@]}"
"""
            return subprocess.run(
                ["bash", "-c", driver, "settings-test", str(path)],
                check=False,
                capture_output=True,
                text=True,
                env={
                    **os.environ,
                    **{
                        f"TEST_{key.upper()}": value for key, value in overrides.items()
                    },
                },
            )

    def test_reuses_custom_paths_resolution_and_no_nvram(self):
        result = self.read_settings(
            "format=1\nconfig=/boot/custom/grub.cfg\nresolution=2560x1440\nnvram=0\nunlock="
            + "1" * 32
            + "\n"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.splitlines(),
            ["/boot/custom/grub.cfg", "2560x1440", "0", "1" * 32],
        )

    def test_explicit_options_override_saved_settings(self):
        result = self.read_settings(
            "format=1\nconfig=/boot/old/grub.cfg\nresolution=2560x1440\nnvram=1\n",
            config="/boot/new/grub.cfg",
            resolution="auto",
            resolution_override="1",
            nvram="0",
            nvram_override="1",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.splitlines()[:3], ["/boot/new/grub.cfg", "auto", "0"]
        )

    def test_settings_are_read_as_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            marker = Path(temporary) / "executed"
            value = f"$(touch {marker})"
            result = self.read_settings(
                f"format=1\nconfig={value}\nresolution=auto\nnvram=0\n"
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.splitlines()[0], value)
            self.assertFalse(marker.exists())

    def test_rejects_invalid_saved_preferences(self):
        for settings in (
            "format=2\n",
            "format=1\nnvram=maybe\n",
            "format=1\nresolution=invalid\n",
            "format=1\nunknown=1\n",
        ):
            with self.subTest(settings=settings):
                self.assertNotEqual(self.read_settings(settings).returncode, 0)


class DeploymentTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="caelestia-deploy-")
        self.addCleanup(self.directory.cleanup)
        self.base = Path(self.directory.name)
        self.payload = self.base / "payload"
        self.payload.mkdir()
        (self.payload / "module").write_text("new module")
        self.image = self.base / "new.efi"
        self.image.write_text("new loader")
        self.settings = self.base / "new.conf"
        self.settings.write_text("new settings")
        self.target = self.base / "boot/grub/caelestia"
        self.target.parent.mkdir(parents=True)
        self.efi = self.base / "esp/EFI/caelestia"
        self.efi.mkdir(parents=True)

    def create_previous(self):
        self.target.mkdir()
        (self.target / "module").write_text("old module")
        (self.efi / "grubx64.efi").write_text("old loader")
        (self.efi / "install.conf").write_text("old settings")

    def deploy(self, failure="", nvram=False, entry_exists=True):
        driver = r"""set -euo pipefail
secure_boot=1
nvram=$TEST_NVRAM
parent=test-disk
partition=1
root() {
  printf '%s\n' "$*" >> "$TEST_LOG"
  case "$1" in
    cp) [[ $2 != -a ]] || return 90 ;;
    install)
      if [[ $TEST_FAILURE == staging && $* == *new.efi* ]]; then return 77; fi ;;
    mv)
      if [[ $TEST_FAILURE == prefix && $* == *caelestia.new.* ]]; then return 77; fi
      if [[ $TEST_FAILURE == loader && $* == *grubx64.efi.new.* ]]; then return 77; fi
      if [[ $TEST_FAILURE == settings && $* == *install.conf.new.* ]]; then return 77; fi ;;
    sbctl) [[ $TEST_FAILURE != signing ]]; return ;;
    efibootmgr)
      if [[ $2 == -v ]]; then
        if [[ $TEST_ENTRY_EXISTS == 1 ]]; then printf '%s\n' 'Boot0001 Caelestia \EFI\caelestia\grubx64.efi'; fi
        return 0
      fi
      [[ $TEST_FAILURE != firmware ]]; return ;;
  esac
  command "$@"
}
"""
        driver += (
            get_function("deploy_installation", ")") + '\ndeploy_installation "$@"\n'
        )
        return subprocess.run(
            [
                "bash",
                "-c",
                driver,
                "deploy-test",
                str(self.payload),
                str(self.image),
                str(self.settings),
                str(self.target),
                str(self.efi),
            ],
            text=True,
            check=False,
            capture_output=True,
            env={
                **os.environ,
                "TEST_FAILURE": failure,
                "TEST_NVRAM": str(int(nvram)),
                "TEST_ENTRY_EXISTS": str(int(entry_exists)),
                "TEST_LOG": str(self.base / "commands.log"),
            },
        )

    def assert_no_pending_files(self):
        self.assertEqual(list(self.base.rglob("*.new.*")), [])

    def test_first_install_publishes_matching_payload_and_settings(self):
        result = self.deploy()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.target / "module").read_text(), "new module")
        self.assertEqual((self.efi / "grubx64.efi").read_text(), "new loader")
        self.assertEqual((self.efi / "install.conf").read_text(), "new settings")
        self.assert_no_pending_files()

    def test_update_keeps_matching_backups(self):
        self.create_previous()
        result = self.deploy()
        self.assertEqual(result.returncode, 0, result.stderr)
        backup = next(self.target.parent.glob("caelestia.backup.*"))
        stamp = backup.name.removeprefix("caelestia.backup.")
        self.assertEqual((backup / "module").read_text(), "old module")
        self.assertEqual(
            (self.efi / f"grubx64.efi.backup.{stamp}").read_text(), "old loader"
        )
        self.assertEqual(
            (self.efi / f"install.conf.backup.{stamp}").read_text(), "old settings"
        )
        self.assert_no_pending_files()

    def test_update_restores_all_files_after_each_failure(self):
        self.create_previous()
        for failure in (
            "staging",
            "prefix",
            "loader",
            "settings",
            "signing",
            "firmware",
        ):
            with self.subTest(failure=failure):
                result = self.deploy(failure, nvram=True, entry_exists=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual((self.target / "module").read_text(), "old module")
                self.assertEqual((self.efi / "grubx64.efi").read_text(), "old loader")
                self.assertEqual(
                    (self.efi / "install.conf").read_text(), "old settings"
                )
                self.assert_no_pending_files()

    def test_failed_first_install_removes_published_files(self):
        result = self.deploy("signing")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.target.exists())
        self.assertFalse((self.efi / "grubx64.efi").exists())
        self.assertFalse((self.efi / "install.conf").exists())
        self.assert_no_pending_files()

    def test_update_does_not_recreate_existing_firmware_entry(self):
        self.create_previous()
        result = self.deploy(nvram=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(
            "efibootmgr --create", (self.base / "commands.log").read_text()
        )

    def test_first_install_creates_missing_firmware_entry(self):
        result = self.deploy(nvram=True, entry_exists=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("efibootmgr --create", (self.base / "commands.log").read_text())


class InstallerOptionsTest(unittest.TestCase):
    def test_help_and_invalid_jobs_do_not_start_a_build(self):
        for arguments in (
            ["update", "--help"],
            ["--build", "--jobs", "0"],
            ["--build", "--jobs", "-1"],
        ):
            with self.subTest(arguments=arguments):
                result = subprocess.run(
                    ["bash", str(ROOT / "install.sh"), *arguments],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertNotIn("Building", result.stdout)
                self.assertEqual(result.returncode, 0 if "--help" in arguments else 1)
