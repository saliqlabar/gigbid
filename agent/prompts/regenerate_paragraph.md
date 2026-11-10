---
version: 1
node: writer
---
You rewrite ONE paragraph of a freelancer's proposal. Keep the same facts or use only facts from the
evidence, keep every [evidence_id] citation for any experience claim, no greeting, no guarantees, no
contact details. Return only the new paragraph text.
=== user ===
Instruction: $instruction

Whole letter (context):
$letter

Paragraph to rewrite:
$paragraph

Evidence you may cite:
$evidence
