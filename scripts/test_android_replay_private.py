"""Check the public workflow boundaries for the Android replay mode."""
import os
import pathlib
import subprocess
import sys
import tarfile
import tempfile
import unittest

import yaml


ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = yaml.safe_load((ROOT / ".github/workflows/farm-replay.yml").read_text(encoding="utf-8"))
STEPS = WORKFLOW["jobs"]["replay"]["steps"]
ANDROID = "${{ inputs.mode == 'android' }}"


def step(name):
    return next(item for item in STEPS if item.get("name") == name)


def run_shell(script, work, **env):
    return subprocess.run(
        [os.environ.get("TEST_BASH", "bash"), "-s"],
        input=script,
        cwd=work,
        env=dict(os.environ, **env),
        capture_output=True,
        text=True,
        check=False,
    )


class PrivateAndroidReplayTests(unittest.TestCase):
    def test_android_is_opt_in_and_requires_an_exact_source_and_archive(self):
        triggers = WORKFLOW.get("on", WORKFLOW.get(True))
        mode = triggers["workflow_dispatch"]["inputs"]["mode"]
        self.assertEqual(mode["options"], ["replay", "record", "android"])
        self.assertEqual(mode["default"], "replay")
        self.assertEqual(set(triggers), {"schedule", "workflow_dispatch"})
        validate = step("Validate the request")["run"]
        self.assertIn("^[0-9a-f]{40}$", validate)
        self.assertIn('[ "$MODE" = android ]', validate)
        self.assertIn("source_ref is required", validate)
        self.assertIn("^android-[0-9]+", validate)
        self.assertIs(step("Check out the private source")["with"]["persist-credentials"], False)
        self.assertIn('[ "$resolved" = "$REQUESTED" ]', step("Pin the exact commit")["run"])

    def test_android_build_and_recorder_only_use_local_simulation(self):
        guard = step("Require the simulated local stack")
        self.assertEqual(guard["if"], ANDROID)
        self.assertIn('env.get("ENV") != "local"', guard["run"])
        self.assertIn('env.get("AI_MODE") != "simulated"', guard["run"])
        build = step("Build the mock flavor")
        self.assertEqual(build["if"], ANDROID)
        self.assertIn(":app:assembleMockDebug", build["run"])
        self.assertIn('> "$RUNNER_TEMP/android-replay-build.log" 2>&1', build["run"])
        record = step("Record and replay the Android journey")
        self.assertEqual(record["if"], ANDROID)
        self.assertIn("-target http://127.0.0.1:8080 -listen 127.0.0.1:4010", record["run"])
        self.assertIn("--local http://127.0.0.1:4010", record["run"])
        self.assertIn("--flow log-typed-meal", record["run"])
        self.assertNotIn("-verify=false", record["run"])
        self.assertIn('> "$RUNNER_TEMP/android-replay-recorder.log" 2>&1', record["run"])
        self.assertNotIn("secrets.", str(record))

    def test_only_the_archive_can_leave_through_private_delivery(self):
        uploads = [item for item in STEPS if item.get("uses", "").startswith("actions/upload-artifact@")]
        self.assertEqual(len(uploads), 1)
        self.assertEqual(uploads[0]["if"], "${{ always() && inputs.mode == 'record' }}")
        release = step("Deliver the Android archive privately")
        self.assertEqual(release["if"], ANDROID)
        self.assertIn("--jq '.private'", release["run"])
        self.assertIn('--draft --target "$SHA"', release["run"])
        self.assertIn('"$ARCHIVE/manifest.json" "$ARCHIVE/exchanges.jsonl"', release["run"])
        self.assertIn('"$bundle" SHA256SUMS >> "$RUNNER_TEMP/android-replay-delivery.log"', release["run"])
        self.assertNotIn("android-replay-journeys", release["run"])
        self.assertNotIn(".apk", release["run"])

    def test_wrong_mode_fails_before_local_stack_build(self):
        script = """
export RUNNER_TEMP="$PWD"
docker() { printf '%s\n' "$COMPOSE_CONFIG"; }
python3() { "$TEST_PYTHON" "$@"; }
""" + step("Require the simulated local stack")["run"]
        for api_mode, worker_mode in (("simulated", "simulated"), ("simulated", "live"), ("record", "simulated")):
            with self.subTest(api=api_mode, worker=worker_mode), tempfile.TemporaryDirectory() as temp:
                config = (
                    '{"services":{"api":{"environment":{"ENV":"local","AI_MODE":"%s"}},'
                    '"worker":{"environment":{"ENV":"local","AI_MODE":"%s"}}}}'
                ) % (api_mode, worker_mode)
                result = run_shell(script, temp, COMPOSE_CONFIG=config, TEST_PYTHON=pathlib.Path(sys.executable).as_posix())
                self.assertEqual(
                    result.returncode, 0 if api_mode == worker_mode == "simulated" else 1, result.stderr + result.stdout
                )
                self.assertNotIn(config, result.stdout + result.stderr)

    def test_archive_delivery_rejects_public_destinations_and_excludes_extras(self):
        script = """
export RUNNER_TEMP="$PWD" GITHUB_STEP_SUMMARY="$PWD/summary"
gh() {
  if [ "$1" = api ]; then printf '%s\n' "$PRIVATE"; return; fi
  printf '%s\n' "$@" >> "$RUNNER_TEMP/delivered"
}
""" + step("Deliver the Android archive privately")["run"]
        for private in ("false", "true"):
            with self.subTest(private=private), tempfile.TemporaryDirectory() as temp:
                work = pathlib.Path(temp)
                archive = "android-1.2.3"
                contents = work / "android-replay-archive" / archive
                contents.mkdir(parents=True)
                for file in ("manifest.json", "exchanges.jsonl", "private.png"):
                    (contents / file).write_text("fixture", encoding="utf-8")
                result = run_shell(
                    script, work, ARCHIVE=archive, PRIVATE=private, SHA="a" * 40,
                    GITHUB_RUN_ID="1", GITHUB_RUN_ATTEMPT="1",
                )
                if private == "false":
                    self.assertEqual(result.returncode, 1)
                    self.assertFalse((work / "delivered").exists())
                    continue
                self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
                delivered = (work / "delivered").read_text(encoding="utf-8").splitlines()
                self.assertEqual(delivered[-2:], [archive + ".tar.gz", "SHA256SUMS"])
                with tarfile.open(contents.parent / delivered[-2]) as bundle:
                    self.assertEqual(bundle.getnames(), [archive + "/manifest.json", archive + "/exchanges.jsonl"])


if __name__ == "__main__":
    unittest.main()
