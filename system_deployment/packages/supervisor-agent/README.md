# Supervisor Agent aggregation

The Pico Agent exposes its local modules on `192.168.217.66:9080`.  The Orin
Agent is the single browser-facing dashboard: it listens on `0.0.0.0:9080`,
so it is available through the Orin LAN address such as `172.16.9.91:9080`.

Install the Pico Agent and all Pico module packages first.  On the Orin,
install `navi_orin_supervisor_agent-1.0.0-arm64.run`.  The Orin Agent then
connects directly to the Pico Agent at `192.168.217.66:9080`; no peer token
or manual credential exchange is required.

The Agent HTTP API and Web page are intentionally unauthenticated.  The Orin
dashboard should therefore only be exposed on a trusted LAN.

Module packages can add a file below `modules.d` without changing the Agent
base configuration.  For example, an Orin module Supervisor at port 19002
would install `/etc/naviai/supervisor-agent/modules.d/robot.json`:

```json
{
  "modules": {
    "robot": {
      "endpoint": "http://192.168.217.100:19002/RPC2"
    }
  }
}
```

The matching module Supervisor must use the Agent's local RPC credential file
and restart after that file is provisioned.  Agent package upgrades preserve
both `modules.d` and all secret files.
