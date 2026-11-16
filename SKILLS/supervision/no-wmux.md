# Supervision — without wmux (default backend)

Served by `$S guide no-wmux`. The board detects wmux at start ("backend wmux" or "none" at the top of the launch output). Without it (Linux, macOS, or wmux not wanted) the whole protocol works, with these differences:

- **Identity**: the agent is started with `SUP_AGENT=<project>/<X>` and `SUP_RUN` (the launch prints the commands); its resume sheet is injected under that name, and its hooks report its state to the board (waiting on a permission included) as with wmux.
- **Board messages** (go-ahead, stop, review, a word from the user): nothing is typed into your terminal; they wait in your **mailbox**, which **every `$S` call shows** (or `$S inbox X`). A message flagged as a user decision is applied at once; run `$S inbox X` at the start of every turn without a `$S` call.
- **Questions to the user**: ask them in your terminal (no `wmux report-agent`). **Browser**: your own tool, never `$S browser`. **Fresh restart**: the user types `/clear`, your sheet comes back on its own.
