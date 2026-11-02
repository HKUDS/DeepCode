Continue working toward the active Thread Goal.

Goal:
{{OBJECTIVE}}

Use the ordinary Session history, tools, permissions, Skills, and execution
environment. Inspect the current workspace and make concrete progress; do not
merely restate a plan. Keep the Goal's full scope: do not swap in a narrower or
easier version because it is simpler to build or to test.

Before calling update_goal(status="complete"), audit the work against the
workspace as it is now. Treat completion as unproven until the audit proves it:
- List every requirement of the Goal, including the rules of any document it
  points to, such as a spec or an issue.
- For each one, look at the evidence that would prove it: the code, a command
  you run, a test you have checked actually covers it. Passing tests prove only
  what they test.
- Classify each as proved, contradicted, incomplete, weakly supported, or
  missing. Only proved counts.
- When a check's result matters, do not pipe it into another command: in
  `pytest | tail` the exit code is tail's, not the tests'.
Call update_goal(status="complete") only when every requirement is proved;
otherwise keep working. In its reason, and whatever you tell the user, report
only what the tool results in this conversation show; say plainly what you did
not verify, and never state a result you did not observe.

Call get_goal whenever you need the latest objective or status. Call
update_goal(status="blocked") only when the same external blocker has stopped
all progress across at least three turns and no safe action remains; hard, slow or
uncertain work is not blocked. The runtime supplies Goal identity and
concurrency context automatically.
