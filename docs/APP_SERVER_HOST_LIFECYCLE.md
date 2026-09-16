# App Server host and connection lifecycle

`ServiceHost` owns one `DeepCodeApplication`; `RpcPeer` owns one client's
protocol state and delivery. Desktop's stdio relay, TUI, headless execution and
the browser authenticate with the [local service](SERVICE_RUNTIME.md).

```text
Desktop native relay / TUI / CLI / Web
  authenticated HTTP and WebSocket
    ServiceHost                     application and shared watchers
      RpcPeer A                     one client's protocol and subscriptions
      RpcPeer B                     independent connection and bounded delivery
```

## Ownership

- `ServiceHost.start()` installs one terminal, Skill, Plugin, MCP, and settings
  notification subscription set. Opening another peer does not add watchers.
- `ServiceHost.connect(send)` starts a peer over complete encoded RPC frames.
  Transport adapters own authentication, framing, and bounded/cancellable I/O.
  The host does not authenticate a caller simply because it can call `connect`.
- `RpcPeer.receive(frame)` serializes that peer's requests and output. Request
  IDs and initialization belong to the peer, not to the application.
- EOF, peer `shutdown`, or a write error releases that peer's subscriptions.
  It does not close the application, cancel Turns, or end terminal sessions.
- `ServiceHost.close()` releases all peers and shared subscriptions, then closes
  the application. Application cleanup failures are surfaced and may be retried.
  Partial startup failure also releases the resources already registered.


## Notification delivery

Shared callbacks enqueue notifications into each initialized peer's bounded
queue. They never write directly to a client's transport. A slow writer can
delay its own RPC output, but cannot block the host from notifying another
client. A write failure closes only the affected peer.

Durable events continue through the existing per-peer `EventBroker`
subscription. Message limits and replay-page fitting are preserved. Queue
overflow emits the existing `server.warning` shape with a
`NOTIFICATION_QUEUE_OVERFLOW` code and a dropped count. Durable history is
recoverable through `event/replay`; transient state notifications require a
fresh read. Terminal output has bounded byte replay with truncation and exit
metadata; input is not automatically replayed.

The peer's send callback must terminate or raise when its transport disconnects.
The host does not close arbitrary caller-owned file descriptors or interrupt a
blocked `readline`. Each transport must unblock its own reader/writer during
shutdown. The stdio relay owns and closes its local connection.

## Verification

`tests/app_server/test_host.py` checks that an admitted task completes with no
clients, a new client can read and replay the result, another client survives
EOF/shutdown/write failure, shared watchers survive disconnect, and a slow
client does not block other clients. It also checks partial startup cleanup,
retryable application cleanup and stdio relay EOF behavior.

Existing App Server protocol, framing, execution, workflow, replay, and
cross-process application tests remain the compatibility baseline.
