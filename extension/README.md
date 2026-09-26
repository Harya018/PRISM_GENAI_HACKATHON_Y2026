# Extension: Samsung device troubleshooting (Section B)

A second use case built on the same agent architecture as the FDB-v3 benchmark submission —
not a separate implementation. `agent/commit_gate.py` and `agent/resolver.py` are imported
directly, unmodified; only the domain (tools, instructions) is new.

## Run it

```
./extension/run_extension.sh
```

or directly:

```
python extension/device_agent.py start
```

Requires the repo-root `.env` (`LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`,
`GOOGLE_API_KEY`) and the same dependencies as the main agent — nothing extra to install.
Connect any LiveKit room client with a microphone and camera enabled.

## What it demonstrates

- **Image-grounded diagnosis**: the agent subscribes to the participant's camera track
  (`RoomInputOptions(video_enabled=True)`) and is instructed to ground its answers in what's
  actually visible (an error code on screen, an LED color, a cable) rather than guessing from
  words alone.
- **Mid-sentence correction**: the same commit gate and block-only resolver from the benchmark
  agent handle a user changing their mind mid-turn ("open Wi-Fi settings — actually, Bluetooth").
- **Interruption handling**: the same no-premature-speech, no-premature-tool-call discipline as
  `agent/instructions.py`.
- **Confirmation before risky actions**: `reset_network_settings` is destructive. The commit gate
  refuses to execute it — code-enforced, not just prompt-trusted — unless the proposing turn's
  transcript contains a plain affirmative reply (`CommitGate.confirm_required`, see
  `agent/commit_gate.py`). A first mention alone can never trigger it, regardless of what the
  model decides to do.
- **Zero duplicate state-changing calls**: the same idempotency guard as the benchmark agent —
  `open_settings`/`reset_network_settings` refuse to re-execute an identical call twice.

## Tools (`mock_device_apis.py`)

| Tool | Kind | Notes |
|---|---|---|
| `lookup_manual(topic)` | read-only | keyword troubleshooting snippets |
| `get_device_status()` | read-only | simulated diagnostics snapshot |
| `open_settings(panel)` | state-modifying | low-risk, navigational |
| `reset_network_settings()` | state-modifying, **destructive** | requires confirmation (see above) |

## Known limitation

The camera-frame wiring (`video_enabled=True`) is implemented against the current
`livekit-agents`/Gemini Live API and follows the same pattern as the benchmark agent's audio
wiring, but has not yet been exercised end-to-end against a live camera feed — the benchmark's
own evaluation harness is audio-only and doesn't provide a way to test this path automatically.
To be verified manually with a real camera-enabled client before submission.
