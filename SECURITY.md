# Security Policy

Please report potential vulnerabilities privately to the repository owner rather than opening a public issue. Include a minimal reproduction, the affected commit, and the impact.

oes-telemetry-bench is an offline Python replay tool: it makes no network requests and starts no server by design. Any change that introduces a network call, remote asset load, or silent acceptance of non-native frame contracts is in scope.
