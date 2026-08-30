# Provider setup and agent handoff

Use this guide when setting up another Mac or continuing provider work with a
different coding agent. Model identifiers, account access, CLI versions, paths,
and quotas must be verified on that machine; the dated findings below are not
portable defaults.

## Start here

1. Read [the agent guide](../AGENTS.md) and
   [model configuration](MODEL_CONFIGURATION.md).
2. Inspect `git status --short` and preserve existing changes. Ensure the target
   checkout contains the provider modules below; a remote install will not
   include uncommitted or unpushed local work.
3. Identify the actual `jarvis` executable and installation with
   `command -v jarvis` and `uv tool list`. An editable install follows source
   changes; a wheel install does not. Neither reloads an already-running daemon.
4. Inspect all three model roles before making changes. Do not equate a saved
   configuration, a running daemon, and a successful model request.

## Supported roles

| Provider | Conversation | Delegated workers | History distillation |
| --- | --- | --- | --- |
| Claude | Native Agent SDK adapter | Native Agent SDK | `claude -p` |
| Codex | Native app-server adapter | `codex exec` | `codex exec` |
| OpenCode | Native CLI plus Jarvis MCP | Not implemented | Native CLI, no tools |
| Antigravity / `agy` | Disabled pending verified integration | Not implemented | Disabled |

`jarvis orchestrator` changes conversation only. `jarvis brain` changes the
default worker only. `jarvis provider` changes all roles and consequently accepts
only Claude or Codex. History input parsers currently read Claude/Codex sessions,
not OpenCode/AGY history; using a provider to distill history does not add a
history importer for that provider.

## Install on another Mac

Jarvis's voice/daemon integration is macOS-specific. Install Homebrew, `uv`,
and the native CLIs needed for the selected roles. A Claude or Codex worker is
still needed for delegated tasks even when the conversation uses OpenCode.

From the intended checkout, choose the appropriate installation:

```sh
uv tool install --editable '.[voice]'
# If Claude support is needed, use this instead:
uv tool install --editable '.[voice,claude]'
jarvis setup-voice
```

Do not install both commands blindly or drop an existing extra during an
upgrade. If a non-editable install remains stale, reinstall from the intended
checkout, preserving extras; `--force --no-cache` avoids reusing an old wheel.
The existing quick installer selects Claude/Codex and does not configure AGY.

Authenticate separately on each Mac through the native provider:

```sh
codex login
claude login
opencode auth login
```

Run only the commands for providers you use. Follow AGY's native
[installation/authentication flow](https://antigravity.google/docs/cli/install/);
do not extract its credentials to build another login mechanism.
Do not copy auth/session-token stores into this repository or another agent's
prompt. Copy only reviewed application settings, then update binary paths,
project paths, vault paths, voice-model paths, and OS permissions.

In `~/.jarvis/config.yaml`, set `opencode_bin` to the absolute result of
`command -v opencode` when using OpenCode. A launchd process does not inherit
interactive shell aliases or necessarily the same PATH. Never copy another
person's absolute home-directory path. `agy_bin` is reserved for AGY setup.

## Select and verify an OpenCode model

```sh
opencode --version
opencode auth list
opencode models
```

The authenticated list matters more than the cached catalog. On the investigated
Mac, the DeepSeek model belonged to OpenCode Go:

```yaml
# Merge into ~/.jarvis/models.yaml; preserve the other roles and model blocks.
orchestrator:
  provider: opencode
  opencode:
    model: opencode-go/deepseek-v4-flash
    variant: ""
```

`opencode-go/...` and `opencode/...` are different provider identifiers. Do not
drop the prefix, substitute a free model, or guess a model version. No
"DeepSeek V3 Flash" was found in the investigated account's model list.
This example is not proof that another account can use it.

Verify parsed configuration from the checkout:

```sh
uv run python -c '
from jarvis.config import Config
c = Config.load()
for name in ("orchestrator", "brain", "sync"):
    role = getattr(c.models, name)
    print(name, role.provider, getattr(role, role.provider).model)
'
```

Before switching a working daemon, make one small request. This check uses the
selected OpenCode conversation model through the tool-free sync transport; the
temporary changes below are in memory and do not save the sync role. It consumes
provider usage and may be billable under the user's authentication.

```sh
uv run python -c '
from jarvis.config import Config
from jarvis.orchestration.external import run_sync
c = Config.load()
c.models.sync.provider = "opencode"
c.models.sync.opencode = c.models.orchestrator.opencode
print(run_sync(c, "Reply exactly READY. Do not use tools.", ""))
'
```

A READY response validates model access, not Jarvis tools or microphone input.
Next run `jarvis chat`, ask it to check `task_status` without creating tasks,
and verify a second turn retains context. Do not use real project edits or
memory writes as smoke checks. Verify the requested native tool actually ran;
a plausible natural-language answer is insufficient.

OpenCode currently uses the v1 JSON CLI protocol, verified against 1.18.25.
OpenCode v2 has not been validated. The adapter disables external plugins and
isolates user configuration while retaining native provider authentication.
Custom provider definitions/auth plugins are therefore not automatically
imported. Organization-managed policy requires separate verification.

## Start/restart and verify voice

After a successful model/tool check, inspect current tasks and approvals before
restarting. Stop or finish active work intentionally; do not interrupt it merely
to apply a model change.

```sh
jarvis install-daemon
launchctl print "gui/$(id -u)/com.jarvis.daemon"
curl --fail --silent http://127.0.0.1:8787/state
```

The installer regenerates the daemon configuration and starts it through launchd.
A provider/model edit alone does not restart it. The dashboard's `brain` value
is the worker provider, not proof of the conversation model. A startup banner
also does not prove a successful provider request. Validate one real response.

Grant Microphone and Input Monitoring/Accessibility to the resolved Jarvis
Python executable when macOS requests it. Recheck after Python/installation
changes. Press Control+Option+J, speak, and pause; verify transcription, reply,
speech, and the follow-up window separately. The default dashboard is
http://127.0.0.1:8787. `jarvis stop` stops both daemon and dashboard.

## Quotas, billing, and failure diagnosis

- Model catalog presence does not establish authentication, quota, or billing.
- On 2026-08-30, OpenCode Go DeepSeek V4 Flash was listed but its request failed
  with a weekly-usage-limit error. Native retries continued until Jarvis's
  240-second timeout. A timeout can therefore hide a quota error.
- Check redacted native provider diagnostics when Jarvis reports only an error
  type. Do not paste complete logs: they may contain prompts, session data,
  credentials, or reasoning. Never route diagnostic/tool JSON to speech.
- Do not enable balance-based spending, buy credits, change billing, or silently
  select another provider/model. Ask the user to choose.
- Jarvis's OpenCode transport ends its child process group on timeout/cancellation.
  An uncaught conversation failure can cause the launchd daemon to restart;
  changing timeouts does not solve an exhausted provider quota.
- Claude SDK use is not itself proof of an account-policy violation or separate
  API billing. Authentication, distribution model, and current provider terms
  matter. API-key use is separately billed; do not offer a custom subscription
  login or intermediate users' subscription tokens. Recheck the
  [Claude authentication rules](https://code.claude.com/docs/en/legal-and-compliance)
  and [SDK billing notice](https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan)
  before giving cost/policy assurances.

## AGY investigation and enablement gate

AGY is not enabled merely because its model responds or an agent name appears
in an initialization event. Its current Jarvis configuration entries are
reserved and execution fails closed.

Verified on CLI 1.1.22:

1. Files under a disposable Git workspace's
   `.agents/agents/<name>/agent.md` were not discovered under the default CLI
   project. The native log reported a fallback to the default agent.
2. `init.agent` nevertheless echoed the requested name. `init.tools` lists a
   broad registry and alone does not establish effective permissions.
3. After explicit `--new-project` registration, `/agents` listed the custom
   agents. Agent discovery must be verified against the registered project.
4. A discovered agent with `tools: [view_file]` returned `TOOL_UNAVAILABLE`
   for a disposable marker-write request, and no marker was created. This supports
   a restricted tool list but is not a formal permission-denial event.
5. A per-agent MCP stub was discovered, and AGY attempted the native
   `call_mcp_tool` for `jarvis/probe_echo`. The permission engine denied
   `mcp(jarvis/probe_echo)`. The final result nevertheless reported
   `status: SUCCESS` with empty text: inspect tool failures, not just final status.
6. The supported permissions TUI rendered blank in the automation PTY, including
   resized/compatibility attempts. No project, Shared, or Global grants were
   changed; the positive MCP round trip is still unverified.

A reproducible discovery check, run from a disposable workspace containing the
agent definition:

```sh
agy --print /agents --output-format json
# Creates a persistent project: obtain user approval before running.
agy --new-project --print /agents --output-format json
# Reuse the returned project ID; do not create a project per model turn.
agy --project PROJECT_ID --print /agents --output-format json
```

Use native Project-scoped permissions for any approved disposable MCP probe, not
Global or Shared permissions. Grant only the named harmless stub tool; never use
`--dangerously-skip-permissions`. Do not guess internal project JSON schemas.
See [AGY agents](https://antigravity.google/docs/cli/commands/agents/),
[projects](https://antigravity.google/docs/cli/projects/), and
[permission scopes](https://antigravity.google/docs/cli/commands/permissions/).

### Current manual unblock

On the investigation Mac only, open this in a real terminal:

```sh
agy --project e5bdb466-3eb3-456f-993e-8dbf3d10999c
```

Enter `/permissions`, select **Project**, select the allowlist, press `A`,
and add exactly `mcp(jarvis/probe_echo)`. Confirm the displayed project/scope
before saving. Do not select Shared or Global. This grants only the disposable
probe; it does not enable production Jarvis tools. Ask before broader grants.

Then rerun the native MCP probe and verify the stub result actually returns,
alongside the disposable write-unavailability check. Native retries can cost
usage; do not loop without a clear pass/fail signal.

The locally verified per-agent stdio MCP shape was:

```yaml
tools: []
mainAgent: true
subagent: false
inheritCustomizations: false
commandExecutionPolicy: "off"
mcpServers:
  - name: jarvis
    command: /usr/bin/python3
    args:
      - /ABSOLUTE/DISPOSABLE/WORKSPACE/mcp_stub.py
```

This is probe evidence, not a complete production adapter or an assumption that
remote HTTP MCP uses the same schema. It requires a real harmless stub server.
Do not copy a model-selected executable or arbitrary MCP config into Jarvis.

### Production enablement checklist

Before enabling AGY in Jarvis, require all of the following:

- The generated custom agent is discovered with no silent fallback.
- A harmless native write to a disposable marker cannot execute.
- An allowed Jarvis MCP stub tool runs and its result reaches the final response.
- Unrelated filesystem, shell, browser, and native delegation capabilities are
  excluded or demonstrably denied; model instructions alone are not enforcement.
- Conversation continuity, tool-free sync, failure handling, and child-process
  cleanup are verified through the installed CLI.
- Project reuse and concurrent sync/conversation configuration cannot overwrite
  one another's generated agent/MCP credentials.
- No provider-global policy/authentication changes, surprise project creation,
  or automatic billing changes are required.

Keep the execution guard until these checks pass. A discovered agent or a
successful read-only model response is not sufficient to remove it.

Once verified, prefer dedicated registered projects reused across runs (separate
conversation and sync grants), with unique per-process agent definitions and
per-agent MCP settings. Do not create a persistent project for every turn or
rewrite shared workspace MCP configuration concurrently. This is an implementation
recommendation, not functionality currently shipped.

## Dated local handoff: 2026-08-30

These are investigation facts, not setup defaults. Refresh on the target machine.

- Saved conversation: `opencode` / `opencode-go/deepseek-v4-flash`.
  Saved worker: Codex. Saved sync: Codex / `gpt-5.6-terra`.
- The existing daemon was deliberately left on its earlier Codex conversation
  while DeepSeek was quota-blocked; the saved and running configurations differed.
  Do not assume a requested restart happened.
- OpenCode `opencode/big-pickle` passed a no-tool request, native Jarvis MCP stub
  call, and two-turn session continuity. That does not certify every model.
- AGY disposable project: `jarvis-agy-isolation-probe`,
  ID `e5bdb466-3eb3-456f-993e-8dbf3d10999c`.
  Workspace: `/private/tmp/jarvis-agy-isolation-probe`.
  Local record: `~/.gemini/config/projects/e5bdb466-3eb3-456f-993e-8dbf3d10999c.json`.
  Local persistence is verified; account-side synchronization is unverified.
  Do not reuse this ID on another Mac or as a production project.
- Temporary probe files and native session/project metadata were retained for
  diagnosis. Evidence in that workspace: `new-project.log`,
  `write-denial.log`, `mcp-roundtrip.log`, and `project-original.json`.
  Probe processes were stopped. Ask before removing the project or associated
  user-owned records. Temporary files may not survive a reboot; this guide
  preserves the findings and commands, not credentials or raw logs.
- Python syntax, wheel build, and OpenCode manual checks passed for the adapter
  implementation. No new test cases were added. Voice interaction and AGY
  production operation were not validated.

## Implementation map for the next coding agent

```text
Conversation
  create_orchestrator()                 # Select the conversation adapter.
    ExternalOrchestrator.run()          # Own one shared core and MCP bridge.
      make_session()                   # Isolate native runtime configuration.
      OpenCodeSession.query()          # Run/resume native CLI and return text.
      MCPBridge._dispatch()            # Validate and dispatch native tool calls.
        OrchestratorCore.execute_tool() # Use existing tasks/memory/approvals.

History distillation
  SecondBrain.sync()                    # Discover/parse existing local sessions.
    SecondBrain.distill()               # Select the independent sync provider.
      run_sync()                       # Run without MCP tools or session resume.
  notion_memory.llm.run_llm()           # Also uses the sync-provider selection.
    run_sync()                         # Same external-provider transport.
```

Provider/config routing lives in `jarvis/config.py`, `jarvis/cli.py`, and
`jarvis/orchestration/__init__.py`. Native external-provider code lives in
`jarvis/orchestration/external.py`, `cli_session.py`, and `mcp_bridge.py`.
Keep Claude optional and lazily imported. Reuse the existing core and tool
schemas rather than a second task manager or text-encoded pretend tool calls.
Do not add OpenCode/AGY worker support or history parsers implicitly.
