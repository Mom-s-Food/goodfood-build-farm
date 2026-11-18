"""Execute the paid entrypoint's validation without credentials or model calls."""
import os
import pathlib
import shutil
import subprocess
import unittest

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = yaml.safe_load((ROOT / ".github/workflows/farm-evals.yml").read_text(encoding="utf-8"))
STEPS = WORKFLOW["jobs"]["evals"]["steps"]


def step(name):
    return next(item for item in STEPS if item.get("name") == name)


class OpenAiEvalsPolicy(unittest.TestCase):
    def validate(self, **overrides):
        env = dict(os.environ, BRANCH="main", MODE="live", SUITE="meal-photo",
                   MAX_CASES="3", CASES="", MODELS="gpt-6-luna", EFFORTS="low",
                   REPEATS="1", MAX_COST="0.25", SPLIT="heldout")
        env.update(overrides)
        bash = shutil.which("bash")
        if os.name == "nt":
            bash = "C:/Program Files/Git/bin/bash.exe"
        return subprocess.run([bash, "-c", step("Validate the request")["run"]],
                              env=env, text=True, capture_output=True)

    def test_luna_is_the_only_default_and_entrypoint_is_manual(self):
        triggers = WORKFLOW.get("on", WORKFLOW.get(True))
        self.assertEqual(set(triggers), {"workflow_dispatch"})
        models = triggers["workflow_dispatch"]["inputs"]["models"]
        self.assertEqual(models["default"], "gpt-6-luna")
        self.assertEqual(models["options"], ["gpt-6-luna"])
        self.assertEqual(self.validate().returncode, 0)

    def test_unknown_legacy_and_multi_model_requests_fail_before_keys(self):
        for model in ("claude-haiku-5-5", "gemini-3.5-flash", "gpt-6-unknown",
                      "gpt-6-luna,gpt-6.1-terra", "openai:gpt-6-luna"):
            with self.subTest(model=model):
                self.assertNotEqual(self.validate(MODELS=model).returncode, 0)

    def test_terra_is_refused_without_verified_pricing(self):
        self.assertNotEqual(self.validate(MODELS="gpt-6.1-terra").returncode, 0)

    def test_paid_step_has_only_openai_key_and_explicit_luna_judge(self):
        paid = step("Run the suites (OpenAI only; costs money)")
        self.assertEqual(paid["env"]["OPENAI_API_KEY"], "${{ secrets.OPENAI_API_KEY_DEV }}")
        self.assertFalse(any("ANTHROPIC" in k or "GEMINI" in k for k in paid["env"]))
        self.assertIn('[ -n "$OPENAI_API_KEY" ]', paid["run"])
        self.assertIn('--models "openai:$MODELS"', paid["run"])
        self.assertIn('--judge-model gpt-6-luna --judge-effort low', paid["run"])
        self.assertIn("PROVIDER_LIVE_MODEL", paid["env"])
        self.assertEqual(paid["env"]["PROVIDER_LIVE_MODEL"], "${{ inputs.models }}")

    def test_batch_probe_refuses_ephemeral_farm_receipt(self):
        result = self.validate(SUITE="provider-batch-live")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("durable resumable receipt", result.stdout)

    def test_old_source_is_rejected_before_the_paid_step(self):
        checkout = step("Check out the private source")
        self.assertEqual(checkout["with"]["fetch-depth"], 0)
        gate = step("Require the reviewed OpenAI evaluation source")
        reviewed = "269555ffa8cd92c9608ddb0b345fbb75e78a9f63"
        triggers = WORKFLOW.get("on", WORKFLOW.get(True))
        self.assertEqual(triggers["workflow_dispatch"]["inputs"]["branch"]["default"], reviewed)
        bash = "C:/Program Files/Git/bin/bash.exe" if os.name == "nt" else shutil.which("bash")
        script = 'git() { printf "%s\\n" "$TEST_SOURCE"; }\n' + gate["run"]
        for source, allowed in ((reviewed, True),
                                ("b514791672b988c8ba19a78394a80b94529d5a84", False),
                                ("0" * 40, False)):
            with self.subTest(source=source):
                result = subprocess.run([bash, "-c", script],
                                        env=dict(os.environ, TEST_SOURCE=source),
                                        text=True, capture_output=True)
                self.assertEqual(result.returncode == 0, allowed)
        self.assertLess(STEPS.index(gate), STEPS.index(step("Run the suites (OpenAI only; costs money)")))

    def test_missing_openai_key_exits_before_creating_report_or_running_binary(self):
        paid = step("Run the suites (OpenAI only; costs money)")
        bash = "C:/Program Files/Git/bin/bash.exe" if os.name == "nt" else shutil.which("bash")
        result = subprocess.run([bash, "-c", paid["run"]],
                                env={"PATH": os.environ["PATH"], "OPENAI_API_KEY": ""},
                                text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("OPENAI_API_KEY_DEV secret is not set", result.stdout)
        self.assertIn("no provider request was sent", result.stdout)


if __name__ == "__main__":
    unittest.main()
