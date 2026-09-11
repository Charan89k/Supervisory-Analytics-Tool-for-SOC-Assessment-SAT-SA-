SAT-SA
Supervisory Analytics Tool for SOC Assessment
=============================================

WHAT THIS IS

SAT-SA helps a supervisor assess whether a Critical Sector Entity
actually ran its Security Operations Centre effectively — using the
entity's own alert and case-management records as the evidence.

It looks for two things that a controls questionnaire cannot show:

  Execution gaps    where the records contradict the stated process:
                    an alert acknowledged but never investigated, an
                    escalation that was required and did not happen,
                    templated investigation notes repeated verbatim.

  Negative space    evidence that should exist and does not: a
                    telemetry source with no coverage, an alert
                    category every peer entity reports and this one
                    never does, cases closed with no notes at all.

Every finding names the rule that produced it and the records it came
from. The scoring is linear arithmetic and is shown in full, so a
supervisor can recompute any entity's risk score by hand.


WHAT THIS IS NOT

It is not a SOC, a SIEM, or a monitoring tool. It does not watch a
network or detect an attack. It assesses how an organisation handled
what its own tools already recorded, after the fact.

It does not judge people. A finding is an indicator for review, worded
as one — "may indicate", never "the analyst failed".

It does not replace supervisory judgement. It puts the evidence in
front of a supervisor in the order most likely to repay attention.


THE AI LAYER, PRECISELY

Optional, local, and strictly supplementary.

The AI may ONLY restate a finding the deterministic rule engine has
already decided, in plainer language. It cannot create a finding,
remove one, change a severity, change a score, change a queue
position, or introduce a fact the evidence does not contain. Nothing
downstream reads its output as anything but display text.

Turn it off and the assessment is identical in substance.

Explanations are never generated automatically. An assessment takes
seconds; explaining findings on a CPU takes minutes. You ask for them
from the Review Queue, on the findings you chose to look at.

Where explanations are shown they are labelled with the model that
produced them. The built-in sample explainer, used for demonstrations,
labels itself as a sample and is never presented as model output.


CONTENTS

  SAT-SA.exe               the application
  _internal\               its libraries and the rule configuration
  _internal\config\        assessment_rules.yaml — every threshold the
                           assessment applies, in plain text, readable
                           and replaceable
  SAT-SA-Setup-AI.ps1      one-time setup for the optional AI layer
  INSTALL.txt              how to install and verify
  README.txt               this file
  ai\                      (optional) offline AI installation files


OFFLINE OPERATION

SAT-SA makes no external network connection of any kind. There is no
licensing check, no update check, no telemetry, and no cloud AI. It
runs on a machine that has never been connected to the internet.

See _internal\docs\OFFLINE_DEPLOYMENT.md for the deployment options and
their accreditation trade-offs, and _internal\docs\AI.md for the AI
layer's constraints in full.
