"""Exercise the workflow's actual probe loop with a virtual clock and fake probes."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/farm-probe.yml"


def probe_loop():
    lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
    step = lines.index("      - name: Probe staging every 5 minutes")
    start = lines.index("        run: |", step) + 1
    end = next(i for i in range(start, len(lines)) if lines[i].startswith("      - name:"))
    return "\n".join(line[10:] if line.startswith("          ") else line for line in lines[start:end])


MOCKS = r'''
date() { if [ "$1" = "+%s" ]; then cat clock; else printf '2026-01-01T00:00:00Z\n'; fi; }
sleep() { echo "$1" >> sleeps; echo $(( $(cat clock) + $1 )) > clock; }
jq() { echo '{}'; }
bash() {
  echo "$(cat clock)" >> starts
  echo '{}' > probe-result/one.json
  echo $(( $(cat clock) + DURATION )) > clock
  [ "$RESULT" = success ]
}
'''


class ProbeCadence(unittest.TestCase):
    def run_loop(self, *, last="", probes="", result="success", duration=1):
        with tempfile.TemporaryDirectory() as temp:
            work = Path(temp)
            (work / "scripts/validation").mkdir(parents=True)
            (work / "scripts/validation/probe.sh").touch()
            (work / "clock").write_text("299", encoding="utf-8")
            (work / "loop.sh").write_text(MOCKS + probe_loop(), encoding="utf-8")
            env = dict(os.environ, QA_SIGNIN_KEY="fake", PROBES=probes, LAST_OK=last,
                       RUN_ID="1", RUN_MINUTES="11", GITHUB_OUTPUT="output",
                       RESULT=result, DURATION=str(duration))
            subprocess.run([os.environ.get("TEST_BASH", "bash"), "loop.sh"], cwd=work,
                           env=env, check=True, capture_output=True, text=True)
            starts = [int(x) for x in (work / "starts").read_text().splitlines()]
            output = (work / "output").read_text()
            return starts, output

    def test_fresh_chain_waits_five_minutes_between_two_failures(self):
        starts, output = self.run_loop(result="failure")
        self.assertEqual(starts, [299, 599])
        self.assertIn("failed_two=true", output)

    def test_successor_waits_before_reporting_a_second_failure(self):
        starts, output = self.run_loop(last="false", result="failure")
        self.assertEqual(starts, [599])
        self.assertIn("failed_two=true", output)

    def test_one_off_starts_immediately_and_respects_intervals(self):
        starts, output = self.run_loop(probes="3")
        self.assertEqual(starts, [299, 599, 899])
        self.assertIn("count=3", output)

    def test_slow_probe_does_not_cause_negative_sleep(self):
        starts, _ = self.run_loop(probes="2", duration=310)
        self.assertEqual(starts, [299, 609])


if __name__ == "__main__":
    unittest.main()
