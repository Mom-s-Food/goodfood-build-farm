"""Static privacy and caller-compatibility checks for the opt-in APK task."""
import pathlib
import unittest

import yaml


ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = yaml.safe_load((ROOT / ".github/workflows/farm-android.yml").read_text(encoding="utf-8"))
STEPS = WORKFLOW["jobs"]["android"]["steps"]
PRIVATE = "${{ env.TASK == 'apk-private' && steps.gate.outputs.run == 'true' }}"


def step(name):
    return next(item for item in STEPS if item.get("name") == name)


class PrivateApkTests(unittest.TestCase):
    def test_existing_tasks_and_default_remain_available(self):
        triggers = WORKFLOW.get("on", WORKFLOW.get(True))
        task = triggers["workflow_dispatch"]["inputs"]["task"]
        self.assertEqual(task["default"], "check")
        self.assertEqual(task["options"], ["check", "record", "apk-private"])
        self.assertEqual(WORKFLOW["env"]["TASK"], "${{ inputs.task || 'check' }}")

    def test_private_build_requires_a_full_source_commit(self):
        validate = step("Validate the request")["run"]
        self.assertIn("^[0-9a-f]{40}$", validate)
        self.assertIn('[ "$TASK" = apk-private ] && [ -z "$REQUESTED" ]', validate)
        checkout = step("Check out the private source (check or apk-private)")
        self.assertEqual(checkout["if"], "${{ env.TASK == 'check' || env.TASK == 'apk-private' }}")
        self.assertIs(checkout["with"]["persist-credentials"], False)

    def test_build_has_no_token_and_uses_the_staging_debug_variant(self):
        build = step("Build the staging debug APK for private delivery")
        self.assertEqual(build["if"], PRIVATE)
        self.assertNotIn("env", build)
        self.assertEqual(build["run"], "./gradlew --no-daemon :app:assembleStagingDebug")
        self.assertEqual(build["working-directory"], "android")

    def test_the_public_artifact_step_is_only_for_check_reports(self):
        uploads = [item for item in STEPS if item.get("uses", "").startswith("actions/upload-artifact@")]
        self.assertEqual(len(uploads), 1)
        self.assertEqual(uploads[0]["if"], "${{ failure() && env.TASK == 'check' && steps.gate.outputs.run == 'true' }}")
        self.assertNotIn(".apk", uploads[0]["with"]["path"])

    def test_delivery_is_a_private_draft_with_source_and_digest(self):
        publish = step("Deliver the APK to the private repository")
        self.assertEqual(publish["if"], PRIVATE)
        self.assertEqual(publish["env"]["GH_TOKEN"], "${{ secrets.PRIVATE_REPO_PAT }}")
        self.assertIn("--jq '.private'", publish["run"])
        self.assertIn('--draft --target "$SHA"', publish["run"])
        self.assertIn('${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}', publish["run"])
        self.assertIn('"$out/SHA256SUMS"', publish["run"])
        scan = step("Scan and describe the private APK")
        self.assertEqual(scan["if"], PRIVATE)
        self.assertIn('scan=$?', scan["run"])
        self.assertIn('APK credential scan failed', scan["run"])
        self.assertIn("app.goodfood.android.staging", scan["run"])
        self.assertIn("sha256sum app-staging-debug.apk > SHA256SUMS", scan["run"])
        self.assertIn('"$tools/apksigner" verify', scan["run"])


if __name__ == "__main__":
    unittest.main()
