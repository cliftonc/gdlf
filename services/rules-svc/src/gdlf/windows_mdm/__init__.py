"""Windows enrolment via signed Provisioning Package (.ppkg).

Asymmetric with the Apple and Android MDM stacks: there's no live two-way
channel after the package is applied. Windows breaks the "real MDM" symmetry
because:

  * VPNv2 CSP doesn't speak WireGuard natively — only IKEv2/SSTP/L2TP.
  * OMA-DM enrollment UI is Pro/Enterprise/Edu only (Home cannot enroll
    into a third-party MDM at all without undocumented APIs).
  * Real MDM wouldn't get us anything WireGuard for Windows doesn't already
    give us locally: the WG client runs each tunnel as a Windows service
    with WFP-kernel kill-switch + autostart pre-logon + a service ACL that
    a Standard User cannot stop.

So the enrolment path is a one-shot, signed Provisioning Package the parent
applies as local Administrator on the kid's PC. The actual containment is
the kid being a Standard User; the parent is the local Administrator.

See `package.build_enroll_ppkg` for the actual artifact assembly.
"""
